"""Runtime harness used by generated tests.

    rig = make_rig("examples/engine_controller/workspace", target="Simulated Hardware")
    rig.reset()
    rig.set("FUEL_PUMP_OK", True)
    t0 = rig.set("IGNITION_SW", "ON")
    rig.expect("ENGINE_RUNNING", "==", True, within_ms=500, since=t0)

Tests run against a *target*: a named configuration in rdtg.yaml that picks one TransportAdapter
per interface (discrete I/O, 1553, Ethernet, ...). Simulated, partially simulated and real
hardware targets differ only in which adapters they use. `TargetBackend` routes signals and
messages to those adapters.

`RequirementsModel` is a different thing: an executable model of the requirements themselves.
It is *not* a target (a pass against it proves nothing about the software); it is only used by
the suite check (`mutation_matrix`) to measure whether the generated tests can detect faults.
"""
from __future__ import annotations

import heapq
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Optional

from .model import Catalog, Pattern, Value, compare, fmt_value
from .project import Project, open_project


class RigError(Exception):
    """Setup could not be established — the test is inconclusive, not failed."""


# --------------------------------------------------------------------------- backends


class Backend(ABC):
    @abstractmethod
    def now(self) -> float: ...
    @abstractmethod
    def wait(self, ms: float) -> None: ...
    @abstractmethod
    def set(self, signal: str, value: Value) -> None: ...
    @abstractmethod
    def get(self, signal: str) -> Value: ...
    @abstractmethod
    def send(self, message: str, fields: dict[str, Value]) -> None: ...
    def reset(self) -> None: ...
    def close(self) -> None: ...


class RequirementsModel(Backend):
    """Executable model of the requirements on a virtual clock (suite check only, not a target).

    faults: {requirement_id: kind} for checking that the generated tests detect wrong behaviour:
        never              never responds
        late / early       responds after max_ms / before min_ms
        stuck              response is asserted from reset, regardless of stimuli
        ignore_conditions  responds to the trigger alone, ignoring the other conditions
    """

    def __init__(self, project: Project, response_fraction: float = 0.5,
                 faults: Optional[dict[str, str]] = None):
        self.project = project
        self.signals = project.catalog.signals()
        self.reqs = [r for r in project.requirements if r.parsed and not r.resolve_errors and r.response]
        self.fraction = response_fraction
        self.faults = faults or {}
        self.log: list[tuple[float, str, Value]] = []
        self.reset()

    def reset(self) -> None:
        self.t = 0.0
        self.state = {n: s.default_initial() for n, s in self.signals.items()}
        self.events: list[tuple[float, int, str, Value]] = []
        self.seq = 0
        self.active: dict[str, bool] = {}
        for r in self.reqs:
            if r.pattern == Pattern.ubiquitous or self.faults.get(r.id) == "stuck":
                for c in r.response:
                    if c.op == "==":
                        self.state[c.signal] = c.value
        # Steady state: requirements already active after reset have already responded.
        for _ in range(len(self.reqs) + 1):
            changed = False
            for r in self.reqs:
                if r.pattern != Pattern.ubiquitous and self._stimuli_hold(r) and self.faults.get(r.id) != "never":
                    for c in r.response:
                        if c.op == "==" and self.state[c.signal] != c.value:
                            self.state[c.signal] = c.value
                            changed = True
            if not changed:
                break
        self.active = {r.id: self._stimuli_hold(r) for r in self.reqs}

    def _stimuli_hold(self, r) -> bool:
        conds = r.stimuli()
        if self.faults.get(r.id) == "ignore_conditions" and r.trigger:
            conds = [r.trigger]
        return all(c.holds(self.state.get(c.signal)) for c in conds)

    def _delay(self, r) -> float:
        cfg = self.project.config
        lo = r.timing.min_ms if r.timing and r.timing.min_ms else 0.0
        hi = r.timing.max_ms if r.timing and r.timing.max_ms else cfg.default_timeout_ms
        fault = self.faults.get(r.id)
        if fault == "late":
            return hi * 1.5 + 1
        if fault == "early":
            return lo * 0.5 if lo else 0.0
        return lo + (hi - lo) * self.fraction

    def _evaluate(self) -> None:
        for r in self.reqs:
            if r.pattern == Pattern.ubiquitous:
                continue
            now_active = self._stimuli_hold(r)
            if now_active and not self.active.get(r.id) and self.faults.get(r.id) not in ("never", "stuck"):
                for c in r.response:
                    if c.op == "==":
                        self.seq += 1
                        heapq.heappush(self.events, (self.t + self._delay(r), self.seq, c.signal, c.value))
            self.active[r.id] = now_active

    def _apply(self, signal: str, value: Value) -> None:
        self.state[signal] = value
        self.log.append((self.t, signal, value))
        self._evaluate()

    def now(self) -> float:
        return self.t

    def wait(self, ms: float) -> None:
        target = self.t + max(ms, 0.0)
        while self.events and self.events[0][0] <= target:
            t, _, sig, val = heapq.heappop(self.events)
            self.t = max(self.t, t)
            self._apply(sig, val)
        self.t = target

    def set(self, signal: str, value: Value) -> None:
        self._apply(signal, value)

    def get(self, signal: str) -> Value:
        return self.state.get(signal)

    def send(self, message: str, fields: dict[str, Value]) -> None:
        for k, v in fields.items():
            self.state[f"{message}.{k}"] = v
        self.log.append((self.t, message, dict(fields)))
        self._evaluate()


class TransportAdapter(ABC):
    """Implement one per bus/IO type (discrete, mil1553, ieee1394, ethernet_udp, ...).

    This is where knowledge from the interface control documents lives: channel names, wire
    encodings, message layouts. `spec` is the catalog Signal (kind, values, units, ...)."""
    def write(self, name: str, value: Value, spec: Any) -> None:
        raise NotImplementedError(f"{type(self).__name__} cannot write signals")
    def read(self, name: str, spec: Any) -> Value:
        raise NotImplementedError(f"{type(self).__name__} cannot read signals")
    def send_message(self, message: Any, fields: dict[str, Value]) -> None:
        raise NotImplementedError(f"{type(self).__name__} cannot send messages")
    def read_message(self, message: Any) -> dict[str, Value]:
        raise NotImplementedError(f"{type(self).__name__} cannot receive messages")
    def reset(self) -> None:
        """Called before every test (e.g. power-cycle the SUT, flush stale messages)."""
    def close(self) -> None: ...


class TargetBackend(Backend):
    """Real-time backend that routes signals/messages to a target's transport adapters.

    adapters: {"discrete": DiscreteIO(...), "mil1553": My1553Card(...), ...}
    Connection points use the "discrete" adapter; message fields use the message's transport.
    """

    def __init__(self, catalog: Catalog, adapters: dict[str, TransportAdapter]):
        self.catalog = catalog
        self.signals = catalog.signals()
        self.adapters = adapters
        self._t0 = time.monotonic()

    def _adapter(self, transport: str) -> TransportAdapter:
        if transport not in self.adapters:
            raise RigError(f"no adapter registered for transport '{transport}'")
        return self.adapters[transport]

    def now(self) -> float:
        return (time.monotonic() - self._t0) * 1000.0

    def wait(self, ms: float) -> None:
        time.sleep(max(ms, 0.0) / 1000.0)

    def reset(self) -> None:
        for a in dict.fromkeys(self.adapters.values()):  # unique, in order
            a.reset()

    def set(self, signal: str, value: Value) -> None:
        self._adapter("discrete").write(signal, value, self.signals[signal])

    def get(self, signal: str) -> Value:
        s = self.signals[signal]
        if s.source == "message":
            msg = self.catalog.message(s.message)
            return self._adapter(s.transport).read_message(msg).get(signal.split(".", 1)[1])
        return self._adapter("discrete").read(signal, s)

    def send(self, message: str, fields: dict[str, Value]) -> None:
        msg = self.catalog.message(message)
        self._adapter(msg.transport).send_message(msg, fields)


# --------------------------------------------------------------------------- rig


class Rig:
    def __init__(self, backend: Backend, catalog: Catalog, poll_ms: float = 1.0):
        self.backend = backend
        self.catalog = catalog
        self.signals = catalog.signals()
        self.poll_ms = poll_ms
        self.transcript: list[str] = []

    def _log(self, msg: str) -> None:
        self.transcript.append(f"[{self.now():9.1f} ms] {msg}")

    def now(self) -> float:
        return self.backend.now()

    def reset(self) -> None:
        self.backend.reset()
        self._log("reset")

    def close(self) -> None:
        self.backend.close()

    def mark(self) -> float:
        return self.now()

    # ---- stimulation
    def set(self, signal: str, value: Value) -> float:
        """Drive a connection point; returns the time the value was applied."""
        if signal not in self.signals:
            raise RigError(f"unknown signal {signal}")
        self.backend.set(signal, value)
        t = self.now()
        self._log(f"set {signal} = {fmt_value(value)}")
        self.backend.wait(self.signals[signal].set_cost_ms)
        return t

    def send(self, message: str, **fields: Value) -> float:
        msg = self.catalog.message(message)
        if msg is None:
            raise RigError(f"unknown message {message}")
        self.backend.send(message, fields)
        t = self.now()
        self._log(f"send {message} {fields}")
        self.backend.wait(msg.send_cost_ms)
        return t

    def wait(self, ms: float) -> None:
        self.backend.wait(ms)

    def action(self, name: str) -> float:
        a = self.catalog.action(name)
        if a is None:
            raise RigError(f"unknown action {name}")
        self._log(f"action {name}")
        for st in a.steps:
            for k, v in (st.set or {}).items():
                self.set(k, v)
            if st.send:
                self.send(st.send["message"], **st.send.get("fields", {}))
            if st.wait_ms:
                self.wait(st.wait_ms)
            for k, v in (st.wait_until or {}).items():
                self.wait_until(k, "==", v, timeout_ms=st.timeout_ms)
        return self.now()

    # ---- observation
    def get(self, signal: str) -> Value:
        return self.backend.get(signal)

    def _poll(self, signal, op, value, deadline) -> bool:
        while True:
            if compare(self.get(signal), op, value):
                return True
            if self.now() >= deadline:
                return False
            self.backend.wait(min(self.poll_ms, deadline - self.now()))

    def wait_until(self, signal: str, op: str, value: Value, timeout_ms: float) -> float:
        """Setup helper: wait for a state. Raises RigError (inconclusive) on timeout."""
        if not self._poll(signal, op, value, self.now() + timeout_ms):
            raise RigError(f"setup failed: {signal} {op} {fmt_value(value)} not reached within "
                           f"{timeout_ms:g} ms (actual {fmt_value(self.get(signal))})")
        self._log(f"reached {signal} {op} {fmt_value(value)}")
        return self.now()

    def check(self, signal: str, op: str, value: Value) -> None:
        actual = self.get(signal)
        if not compare(actual, op, value):
            raise RigError(f"precondition failed: expected {signal} {op} {fmt_value(value)}, "
                           f"actual {fmt_value(actual)}")

    def expect(self, signal: str, op: str, value: Value, within_ms: float,
               since: Optional[float] = None) -> float:
        start = self.now() if since is None else since
        ok = self._poll(signal, op, value, start + within_ms)
        actual = self.get(signal)
        if not ok:
            raise AssertionError(f"{signal} {op} {fmt_value(value)} not observed within "
                                 f"{within_ms:g} ms (actual {fmt_value(actual)})")
        dt = self.now() - start
        self._log(f"observed {signal} {op} {fmt_value(value)} after {dt:.1f} ms")
        return dt

    def expect_not(self, signal: str, op: str, value: Value, for_ms: float,
                   since: Optional[float] = None) -> None:
        start = self.now() if since is None else since
        deadline = start + for_ms
        while True:
            if compare(self.get(signal), op, value):
                raise AssertionError(f"{signal} {op} {fmt_value(value)} occurred "
                                     f"{self.now() - start:.1f} ms after stimulus; expected it not "
                                     f"to within {for_ms:g} ms")
            if self.now() >= deadline:
                break
            self.backend.wait(min(self.poll_ms, deadline - self.now()))
        self._log(f"confirmed {signal} {op} {fmt_value(value)} absent for {for_ms:g} ms")


# --------------------------------------------------------------------------- targets


def load_symbol(spec: str, project_root: Path):
    """'pkg.module:Name' or 'path/to/file.py:Name' (path relative to the workspace folder)."""
    import importlib
    import importlib.util
    import sys
    mod, _, name = spec.partition(":")
    if not name:
        raise RigError(f"'{spec}' must look like 'module:Name' or 'file.py:Name'")
    if mod.endswith(".py"):
        path = Path(mod)
        if not path.is_absolute():
            path = project_root / path
        path = path.resolve()
        if not path.is_file():
            raise RigError(f"{path} not found (referenced by '{spec}')")
        key = f"rdtg_rig_{abs(hash(str(path)))}"
        if key not in sys.modules:  # load once so module state is shared across tests
            spec_ = importlib.util.spec_from_file_location(key, path)
            module = importlib.util.module_from_spec(spec_)
            sys.modules[key] = module
            try:
                spec_.loader.exec_module(module)
            except Exception:
                del sys.modules[key]
                raise
        module = sys.modules[key]
    else:
        module = importlib.import_module(mod)
    try:
        return getattr(module, name)
    except AttributeError:
        raise RigError(f"'{name}' not found in {mod}") from None


# The bench is exclusive: only one target is open per process. Re-opening the same target with
# the same parameters reuses its adapters (so a pytest session connects once).
_active: dict[str, Any] = {"key": None, "adapters": None, "cleanup": None}


def release_target() -> None:
    """Close the open target's adapters and run its cleanup (e.g. stop the software under test)."""
    adapters, cleanup = _active["adapters"], _active["cleanup"]
    _active.update(key=None, adapters=None, cleanup=None)
    for a in dict.fromkeys((adapters or {}).values()):
        try:
            a.close()
        except Exception:
            pass
    if callable(cleanup):
        cleanup()


def open_target(project: Project, name: Optional[str] = None,
                params: Optional[dict[str, Any]] = None) -> TargetBackend:
    try:
        target = project.config.target(name)
    except KeyError as e:
        raise RigError(str(e.args[0])) from None
    merged = {**target.params, **(params or {})}
    root = Path(project.root or ".").resolve()
    key = repr((str(root), target.model_dump(), sorted(merged.items())))
    if _active["key"] != key:
        release_target()
        cleanup = load_symbol(target.start, root)(target, merged) if target.start else None
        adapters: dict[str, TransportAdapter] = {}
        try:
            for transport, spec in target.adapters.items():
                spec = dict(spec)
                cls_spec = spec.pop("type", None)
                if not cls_spec:
                    raise RigError(f"target '{target.name}': adapter for '{transport}' has no type")
                cls = load_symbol(cls_spec, root)
                try:
                    adapters[transport] = cls(**spec)
                except RigError:
                    raise
                except Exception as e:  # driver missing, device not reachable, bad kwargs ...
                    raise RigError(f"target '{target.name}': cannot open {transport} adapter "
                                   f"({cls_spec}): {e}") from e
        except Exception:
            for a in adapters.values():
                try:
                    a.close()
                except Exception:
                    pass
            if callable(cleanup):
                cleanup()
            raise
        _active.update(key=key, adapters=adapters, cleanup=cleanup)
    return TargetBackend(project.catalog, _active["adapters"])


def parse_params(spec: Optional[str]) -> dict[str, str]:
    """'bugs=slow_spinup;foo=1' -> {...} (';' separated, so values may contain commas)."""
    out = {}
    for part in (spec or "").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def make_rig(project_dir: str | Path, target: Optional[str] = None,
             params: Optional[dict[str, Any]] = None) -> Rig:
    """Defaults come from RDTG_TARGET / RDTG_TARGET_PARAMS, then rdtg.yaml default_target."""
    import os
    project = open_project(project_dir)
    target = target or os.environ.get("RDTG_TARGET") or None
    if params is None:
        params = parse_params(os.environ.get("RDTG_TARGET_PARAMS"))
    return Rig(open_target(project, target, params), project.catalog)


# --------------------------------------------------------------------------- in-process execution


def execute(test, rig: Rig) -> dict:
    """Interpret a TestCase's steps directly (same semantics as the generated pytest code).

    Returns {"id", "status": passed|failed|inconclusive, "message", "transcript"}."""
    rig.transcript = []
    rig.reset()
    t0: Optional[float] = None
    stim = [s for s in test.steps if s.phase == "stimulus"]
    status, message = "passed", ""
    try:
        for s in test.steps:
            if s.phase == "verify" and t0 is None:
                t0 = rig.mark()
            if s.kind == "set":
                t = rig.set(s.signal, s.value)
            elif s.kind == "send":
                t = rig.send(s.message, **s.fields)
            elif s.kind == "action":
                t = rig.action(s.action)
            elif s.kind == "wait":
                rig.wait(s.ms); t = rig.now()
            elif s.kind == "wait_until":
                t = rig.wait_until(s.signal, s.op, s.value, timeout_ms=s.ms)
            elif s.kind == "check":
                rig.check(s.signal, s.op, s.value); t = rig.now()
            elif s.kind == "expect":
                rig.expect(s.signal, s.op, s.value, within_ms=s.ms, since=t0); t = rig.now()
            elif s.kind == "expect_not":
                rig.expect_not(s.signal, s.op, s.value, for_ms=s.ms, since=t0); t = rig.now()
            if stim and s is stim[-1]:
                t0 = t
    except RigError as e:
        status, message = "inconclusive", str(e)
    except AssertionError as e:
        status, message = "failed", str(e)
    return {"id": test.id, "status": status, "message": message, "transcript": list(rig.transcript)}



FAULT_KINDS = ["never", "late", "early", "stuck", "ignore_conditions"]
_FAULT_APPLIES = {
    "late": lambda r: bool(r.timing and r.timing.max_ms),
    "early": lambda r: bool(r.timing and r.timing.min_ms),
    "ignore_conditions": lambda r: bool(r.trigger and r.conditions),
}


def mutation_matrix(project: Project, suite) -> dict:
    """Suite check: inject every applicable fault into the requirements model, one at a time, and run the suite.

    caught  = at least one test failed; flagged = only inconclusive results; MISSED = all passed;
    equivalent = the fault can never be observed on a system that obeys the other requirements
                 (e.g. ignoring a condition that always holds whenever the trigger does)."""
    from .feasibility import Feasibility
    from .graph import negate
    feas = Feasibility(project)
    rows: dict[str, dict[str, str]] = {}
    counts = {"caught": 0, "flagged": 0, "MISSED": 0, "equivalent": 0}
    for r in project.requirements:
        if not (r.parsed and not r.resolve_errors and r.response):
            continue
        row = {}
        for k in FAULT_KINDS:
            if not _FAULT_APPLIES.get(k, lambda r: True)(r):
                row[k] = "n/a"
                continue
            if k == "ignore_conditions" and all(
                    feas.check([r.trigger] + [negate(c) if c is cond else c for c in r.conditions])
                    for cond in r.conditions):
                counts["equivalent"] += 1
                row[k] = "equivalent"
                continue
            rig = Rig(RequirementsModel(project, faults={r.id: k}), project.catalog)
            statuses = {execute(t, rig)["status"] for t in suite.tests}
            verdict = ("caught" if "failed" in statuses
                       else "flagged" if "inconclusive" in statuses else "MISSED")
            counts[verdict] += 1
            row[k] = verdict
        rows[r.id] = row
    return {"rows": rows, "counts": counts}
