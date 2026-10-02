"""ECU-7 engine controller — a stand-in for real embedded software under test.

Written independently of the RDTG tool (it imports nothing from it). It implements the behaviour
in ICD.md with its own state machine, real-time control loop, and socket interfaces:

    TCP  7001   bench I/O box (discrete / analog channels)
    UDP  7002   emulated MIL-STD-1553 BC->RT commands
    UDP  5005   ENG_STATUS broadcast at 50 Hz (sent to 127.0.0.1)

Run:   python engine_controller.py [--bug NAME ...]

Known-bug switches (to show what the generated tests catch):
    fuel_low_boundary   FUEL_LOW uses <= 20 psi instead of < 20 psi
    no_fuel_pump_check  engine cranks even when the fuel pump is not OK
    slow_spinup         cranking takes 700 ms instead of 300 ms
    slow_mode           mode transition takes 120 ms instead of 40 ms
    maint_ignored       maintenance jumper does not inhibit start
"""
from __future__ import annotations

import argparse
import socket
import socketserver
import struct
import threading
import time

BUGS = {"fuel_low_boundary", "no_fuel_pump_check", "slow_spinup", "slow_mode", "maint_ignored"}

INPUT_DEFAULTS = {"DI_IGN": 0.0, "DI_FUEL_PUMP_OK": 0.0, "DI_MAINT": 0.0,
                  "AI_THROTTLE": 0.0, "AI_FUEL_PRESS": 45.0}
OUTPUTS = ("DO_ENG_RUN", "DO_FUEL_LOW", "DO_START_INH", "DO_OVERSPEED", "DO_PUMP_CMD", "DO_READY")

RT_ADDRESS = 5
SA_MODE_CMD = 3
MODES = {0: "NORMAL", 1: "MAINT", 2: "TEST"}

TICK_S = 0.005
STATUS_PERIOD_S = 0.020


def now_ms() -> float:
    return time.monotonic() * 1000.0


class Controller:
    def __init__(self, bugs: set[str]):
        self.bugs = bugs
        self.lock = threading.RLock()
        self.reset()

    # ------------------------------------------------------------------ state
    def reset(self) -> None:
        with self.lock:
            self.io = dict(INPUT_DEFAULTS)
            self.io.update({o: 0.0 for o in OUTPUTS})
            self.engine = "OFF"           # OFF | CRANKING | RUNNING | SPINDOWN
            self.engine_t = 0.0
            self.mode = "NORMAL"
            self.pending_mode: tuple[str, float] | None = None
            self.low_press_since: float | None = None
            self.inhibit_since: float | None = None
            self.overspeed_since: float | None = None
            self.ign_changed_at = 0.0
            self.last_ign = False
            self.pump_cmd = False
            self.ready_since: float | None = None
            self.rpm_valid = False

    # ------------------------------------------------------------------ I/O
    def set_input(self, ch: str, value: float) -> None:
        with self.lock:
            if ch not in INPUT_DEFAULTS:
                raise KeyError(f"{ch} is not an input channel")
            self.io[ch] = value

    def get(self, ch: str) -> float:
        with self.lock:
            return self.io[ch]

    def command_mode(self, code: int) -> None:
        delay = 120.0 if "slow_mode" in self.bugs else 40.0
        with self.lock:
            self.pending_mode = (MODES.get(code, self.mode), now_ms() + delay)

    # ------------------------------------------------------------------ control law
    def step(self) -> None:
        t = now_ms()
        with self.lock:
            ign = self.io["DI_IGN"] >= 0.5
            fuel_ok = self.io["DI_FUEL_PUMP_OK"] >= 0.5
            maint = self.io["DI_MAINT"] >= 0.5
            if "no_fuel_pump_check" in self.bugs:
                fuel_ok = True
            start_permitted = ign and fuel_ok and (not maint or "maint_ignored" in self.bugs)
            crank_ms = 700.0 if "slow_spinup" in self.bugs else 300.0

            if self.engine == "OFF":
                if start_permitted:
                    self.engine, self.engine_t = "CRANKING", t
            elif self.engine == "CRANKING":
                if not start_permitted:
                    self.engine = "OFF"
                elif t - self.engine_t >= crank_ms:
                    self.engine = "RUNNING"
            elif self.engine == "RUNNING":
                if not (ign and fuel_ok):
                    self.engine, self.engine_t = "SPINDOWN", t
            elif self.engine == "SPINDOWN":
                if t - self.engine_t >= 200.0:
                    self.engine = "OFF"
            running = self.engine == "RUNNING"

            # start inhibit, 30 ms filter
            if maint and ign:
                self.inhibit_since = self.inhibit_since or t
                inhibit = t - self.inhibit_since >= 30.0
            else:
                self.inhibit_since, inhibit = None, False

            # fuel low caution, 50 ms debounce
            p = self.io["AI_FUEL_PRESS"]
            low = p <= 20.0 if "fuel_low_boundary" in self.bugs else p < 20.0
            if low:
                self.low_press_since = self.low_press_since if self.low_press_since is not None else t
                fuel_low = t - self.low_press_since >= 50.0
            else:
                self.low_press_since, fuel_low = None, False

            # overspeed caution: running, ignition on, throttle above 95 % for 20 ms
            if running and ign and self.io["AI_THROTTLE"] > 95.0:
                self.overspeed_since = self.overspeed_since if self.overspeed_since is not None else t
                overspeed = t - self.overspeed_since >= 20.0
            else:
                self.overspeed_since, overspeed = None, False

            # fuel pump command follows the ignition switch after a 20 ms relay delay
            if ign != self.last_ign:
                self.last_ign, self.ign_changed_at = ign, t
            if t - self.ign_changed_at >= 20.0:
                self.pump_cmd = ign

            # ready lamp: ignition on and pump commanded, 30 ms filter
            if ign and self.pump_cmd:
                self.ready_since = self.ready_since if self.ready_since is not None else t
                ready = t - self.ready_since >= 30.0
            else:
                self.ready_since, ready = None, False

            if self.pending_mode and t >= self.pending_mode[1]:
                self.mode, self.pending_mode = self.pending_mode[0], None

            self.rpm_valid = running and self.io["AI_THROTTLE"] > 20.0
            self.io["DO_ENG_RUN"] = 1.0 if running else 0.0
            self.io["DO_START_INH"] = 1.0 if inhibit else 0.0
            self.io["DO_FUEL_LOW"] = 1.0 if fuel_low else 0.0
            self.io["DO_OVERSPEED"] = 1.0 if overspeed else 0.0
            self.io["DO_PUMP_CMD"] = 1.0 if self.pump_cmd else 0.0
            self.io["DO_READY"] = 1.0 if ready else 0.0

    def status_flags(self) -> int:
        with self.lock:
            return ((1 if self.rpm_valid else 0)
                    | (2 if self.mode == "MAINT" else 0)
                    | (4 if self.engine == "RUNNING" else 0))


# ---------------------------------------------------------------------- servers


def make_io_server(ctl: Controller, port: int) -> socketserver.ThreadingTCPServer:
    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            for raw in self.rfile:
                parts = raw.decode("ascii", "replace").split()
                if not parts:
                    continue
                try:
                    cmd = parts[0].upper()
                    if cmd == "SET":
                        ctl.set_input(parts[1], float(parts[2]))
                        reply = "OK"
                    elif cmd == "GET":
                        reply = f"{ctl.get(parts[1]):g}"
                    elif cmd == "RESET":
                        ctl.reset()
                        time.sleep(0.010)
                        reply = "OK"
                    elif cmd == "LIST":
                        reply = " ".join(list(INPUT_DEFAULTS) + list(OUTPUTS))
                    else:
                        reply = f"ERR unknown command {cmd}"
                except (KeyError, IndexError, ValueError) as e:
                    reply = f"ERR {e}"
                self.wfile.write((reply + "\n").encode("ascii"))

    socketserver.ThreadingTCPServer.allow_reuse_address = True
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", port), Handler)
    srv.daemon_threads = True
    return srv


def bus_1553_loop(ctl: Controller, port: int) -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", port))
    while True:
        data, _ = s.recvfrom(64)
        if len(data) < 2:
            continue
        (cw,) = struct.unpack(">H", data[:2])
        rt, tr, sa, wc = cw >> 11, (cw >> 10) & 1, (cw >> 5) & 0x1F, cw & 0x1F
        words = struct.unpack(f">{(len(data) - 2) // 2}H", data[2:2 + 2 * ((len(data) - 2) // 2)])
        if rt != RT_ADDRESS or tr != 0:
            continue
        if sa == SA_MODE_CMD and wc == 1 and words:
            ctl.command_mode(words[0])


def status_loop(ctl: Controller, port: int) -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    seq = 0
    next_t = time.monotonic()
    while True:
        s.sendto(struct.pack(">BBHHH", 0xE5, 1, seq & 0xFFFF, ctl.status_flags(), 0), ("127.0.0.1", port))
        seq += 1
        next_t += STATUS_PERIOD_S
        time.sleep(max(0.0, next_t - time.monotonic()))


def control_loop(ctl: Controller) -> None:
    next_t = time.monotonic()
    while True:
        ctl.step()
        next_t += TICK_S
        delay = next_t - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        else:
            next_t = time.monotonic()


def main() -> None:
    ap = argparse.ArgumentParser(description="ECU-7 engine controller (bench build)")
    ap.add_argument("--bug", action="append", default=[], choices=sorted(BUGS))
    ap.add_argument("--io-port", type=int, default=7001)
    ap.add_argument("--bus-port", type=int, default=7002)
    ap.add_argument("--status-port", type=int, default=5005)
    a = ap.parse_args()

    ctl = Controller(set(a.bug))
    srv = make_io_server(ctl, a.io_port)
    for target, args in ((control_loop, (ctl,)), (bus_1553_loop, (ctl, a.bus_port)),
                         (status_loop, (ctl, a.status_port))):
        threading.Thread(target=target, args=args, daemon=True).start()
    print(f"ECU-7 running: I/O tcp:{a.io_port}  1553 udp:{a.bus_port}  status -> udp:{a.status_port}"
          f"  bugs={sorted(a.bug) or 'none'}", flush=True)
    try:
        srv.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
