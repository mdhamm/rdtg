"""Rig integration for ECU-7 (see ../software/ICD.md).

The code a test engineer writes once per program: adapters that translate catalog signal and
message names into ECU-7's wire formats. Targets in rdtg.yaml choose which adapters to use for
each interface and where they connect; generated tests never change.

    start_desktop_ecu   `start` hook for the Simulated Hardware target: launches
                        ../software/engine_controller.py on this PC (params: bugs=a,b)
    IoBoxAdapter        discrete/analog channels via the bench I/O box protocol (ICD §1)
    Bus1553Adapter      emulated MIL-STD-1553 BC->RT commands over UDP (ICD §2)
    EthernetStatusAdapter  listens for ENG_STATUS datagrams (ICD §3)
"""
from __future__ import annotations

import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

from rdtg.harness import RigError, TransportAdapter

SOFTWARE = Path(__file__).resolve().parents[2] / "software" / "engine_controller.py"

# ICD §1.1: catalog signal -> I/O box channel
CHANNELS = {
    "IGNITION_SW": "DI_IGN",
    "FUEL_PUMP_OK": "DI_FUEL_PUMP_OK",
    "MAINT_MODE": "DI_MAINT",
    "THROTTLE": "AI_THROTTLE",
    "FUEL_PRESSURE": "AI_FUEL_PRESS",
    "ENGINE_RUNNING": "DO_ENG_RUN",
    "FUEL_LOW": "DO_FUEL_LOW",
    "START_INHIBIT": "DO_START_INH",
    "OVERSPEED": "DO_OVERSPEED",
    "FUEL_PUMP_CMD": "DO_PUMP_CMD",
    "READY_LAMP": "DO_READY",
}
# ICD §1.1: enum encodings on discrete channels
ENUM_WIRE = {"IGNITION_SW": {"OFF": 0, "ON": 1}}
# ICD §2: MODE_CMD data word
MODE_CODES = {"NORMAL": 0, "MAINT": 1, "TEST": 2}


# ---------------------------------------------------------------------- adapters


class IoBoxAdapter(TransportAdapter):
    """ICD §1 — line-based TCP protocol to the bench I/O box."""

    def __init__(self, host: str = "127.0.0.1", port: int = 7001, timeout_s: float = 2.0):
        try:
            self.sock = socket.create_connection((host, port), timeout=timeout_s)
        except OSError as e:
            raise RigError(f"I/O box not reachable at {host}:{port} ({e})") from None
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.rfile = self.sock.makefile("rb")
        self.lock = threading.Lock()

    def _cmd(self, line: str) -> str:
        with self.lock:
            self.sock.sendall((line + "\n").encode("ascii"))
            reply = self.rfile.readline().decode("ascii").strip()
        if not reply:
            raise RigError("I/O box closed the connection")
        if reply.startswith("ERR"):
            raise RigError(f"I/O box: {line!r} -> {reply}")
        return reply

    def write(self, name, value, spec):
        ch = CHANNELS[name]
        if name in ENUM_WIRE:
            wire = ENUM_WIRE[name][value]
        elif isinstance(value, bool):
            wire = int(value)
        else:
            wire = float(value)
        self._cmd(f"SET {ch} {wire}")

    def read(self, name, spec):
        raw = float(self._cmd(f"GET {CHANNELS[name]}"))
        if name in ENUM_WIRE:
            return {v: k for k, v in ENUM_WIRE[name].items()}[int(raw)]
        if spec.kind == "discrete":
            return raw >= 0.5
        return raw

    def reset(self):
        self._cmd("RESET")  # power-cycles the ECU

    def close(self):
        self.sock.close()


class Bus1553Adapter(TransportAdapter):
    """ICD §2 — emulated MIL-STD-1553 BC->RT receive commands over UDP."""

    def __init__(self, host: str = "127.0.0.1", port: int = 7002):
        self.addr = (host, port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send_message(self, message, fields):
        if message.name != "CMD_MODE":
            raise RigError(f"no 1553 encoding defined for {message.name}")
        rt, sa = message.address["rt"], message.address["sa"]
        words = [MODE_CODES[fields["mode"]]]
        command = (rt << 11) | (0 << 10) | (sa << 5) | len(words)
        self.sock.sendto(struct.pack(f">H{len(words)}H", command, *words), self.addr)

    def close(self):
        self.sock.close()


class EthernetStatusAdapter(TransportAdapter):
    """ICD §3 — listens for the 50 Hz ENG_STATUS datagrams and keeps the latest one."""

    def __init__(self, host: str = "127.0.0.1", port: int = 5005):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.sock.bind((host, port))
        except OSError as e:
            raise RigError(f"cannot listen for ENG_STATUS on udp:{port} ({e}). Only one test run "
                           f"can use the bench at a time.") from None
        self.latest: dict = {}
        self.received_at = 0.0
        self.cond = threading.Condition()
        threading.Thread(target=self._listen, daemon=True).start()

    def _listen(self):
        while True:
            try:
                data, _ = self.sock.recvfrom(64)
            except OSError:
                return  # socket closed
            if len(data) < 8:
                continue
            magic, version, seq, flags, _ = struct.unpack(">BBHHH", data[:8])
            if magic != 0xE5 or version != 1:
                continue
            with self.cond:
                self.latest = {"seq": seq, "rpm_valid": bool(flags & 1),
                               "maint_active": bool(flags & 2), "eng_run": bool(flags & 4)}
                self.received_at = time.monotonic()
                self.cond.notify_all()

    def read_message(self, message):
        if message.name != "ENG_STATUS":
            raise RigError(f"no Ethernet decoding defined for {message.name}")
        with self.cond:
            return dict(self.latest)

    def reset(self):
        # Discard status sent before the ECU reset; wait for a fresh packet.
        t = time.monotonic()
        with self.cond:
            if not self.cond.wait_for(lambda: self.received_at > t + 0.001, timeout=1.0):
                raise RigError("no ENG_STATUS received from the ECU within 1 s")

    def close(self):
        self.sock.close()


# ---------------------------------------------------------------------- start hook


def _port_open(host: str, port: int) -> bool:
    try:
        socket.create_connection((host, port), timeout=0.2).close()
        return True
    except OSError:
        return False


def start_desktop_ecu(target, params):
    """Launch ECU-7 on this PC for the Simulated Hardware target.

    params: bugs  comma-separated --bug switches of engine_controller.py (default none)
    Returns a cleanup callable that stops the process."""
    io = target.adapters.get("discrete", {})
    host, port = io.get("host", "127.0.0.1"), io.get("port", 7001)
    if _port_open(host, port):
        raise RigError(f"something is already listening on {host}:{port}; stop it (another ECU-7 "
                       f"instance or test run) so this target can start its own")
    args = [sys.executable, str(SOFTWARE), "--io-port", str(port)]
    for bug in filter(None, (b.strip() for b in str(params.get("bugs") or "").split(","))):
        args += ["--bug", bug]
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    deadline = time.monotonic() + 10
    while not _port_open(host, port):
        if proc.poll() is not None:
            raise RigError(f"ECU-7 exited: {proc.stderr.read().decode(errors='replace').strip()[-300:]}")
        if time.monotonic() > deadline:
            proc.kill()
            raise RigError("ECU-7 did not start within 10 s")
        time.sleep(0.05)

    def stop():
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    return stop
