"""Core data model: interface catalog, requirements, project config, test cases."""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

Value = Any  # bool | int | float | str


# --------------------------------------------------------------------------- catalog


class SignalKind(str, Enum):
    discrete = "discrete"  # boolean
    enum = "enum"
    analog = "analog"


class Direction(str, Enum):
    input = "input"    # the test rig drives it into the SUT
    output = "output"  # the SUT drives it; the rig observes


class ConnectionPoint(BaseModel):
    name: str
    kind: SignalKind = SignalKind.discrete
    direction: Direction = Direction.input
    values: Optional[list[Value]] = None  # enum values; discrete defaults to [False, True]
    min: Optional[float] = None
    max: Optional[float] = None
    resolution: float = 1.0               # smallest meaningful step, used for boundary values
    units: Optional[str] = None
    initial: Optional[Value] = None       # value after rig reset
    set_cost_ms: float = 10.0             # time to actuate
    settle_ms: float = 0.0                # time before the SUT reliably sees the change
    description: str = ""


class MessageField(BaseModel):
    name: str
    kind: SignalKind = SignalKind.analog
    type: str = "uint16"
    values: Optional[list[Value]] = None
    min: Optional[float] = None
    max: Optional[float] = None
    resolution: float = 1.0
    units: Optional[str] = None
    initial: Optional[Value] = None
    description: str = ""


class Message(BaseModel):
    """A message from a software interface control document, normalized across transports."""
    name: str
    transport: str = "ethernet_udp"       # mil1553 | ieee1394 | ethernet_udp | ...
    direction: Direction = Direction.input  # input = rig -> SUT
    address: dict[str, Any] = Field(default_factory=dict)  # transport-specific, opaque to core
    rate_hz: Optional[float] = None
    send_cost_ms: float = 5.0
    fields: list[MessageField] = Field(default_factory=list)
    description: str = ""


class ActionStep(BaseModel):
    set: Optional[dict[str, Value]] = None
    send: Optional[dict[str, Any]] = None        # {message: NAME, fields: {...}}
    wait_ms: Optional[float] = None
    wait_until: Optional[dict[str, Value]] = None
    timeout_ms: float = 1000.0


class Action(BaseModel):
    """A named multi-step procedure that brings the SUT into some state."""
    name: str
    steps: list[ActionStep] = Field(default_factory=list)
    requires: dict[str, Value] = Field(default_factory=dict)
    produces: dict[str, Value] = Field(default_factory=dict)
    cost_ms: float = 1000.0
    description: str = ""


class Signal(BaseModel):
    """Unified view of anything that can be set or observed: a connection point or `MSG.field`."""
    name: str
    kind: SignalKind
    direction: Direction
    values: Optional[list[Value]] = None
    min: Optional[float] = None
    max: Optional[float] = None
    resolution: float = 1.0
    units: Optional[str] = None
    initial: Optional[Value] = None
    set_cost_ms: float = 0.0
    source: Literal["connection_point", "message"] = "connection_point"
    message: Optional[str] = None
    transport: Optional[str] = None

    def domain(self) -> Optional[list[Value]]:
        if self.kind == SignalKind.discrete:
            return [False, True]
        if self.kind == SignalKind.enum:
            return list(self.values or [])
        return None

    def default_initial(self) -> Value:
        if self.initial is not None:
            return self.initial
        if self.kind == SignalKind.discrete:
            return False
        if self.kind == SignalKind.enum and self.values:
            return self.values[0]
        return self.min if self.min is not None else 0


class Constraint(BaseModel):
    """A combination of conditions that can never hold at the same time (physics, wiring, ...),
    written like a requirement's conditions: "FUEL_PUMP_OK is false and FUEL_PRESSURE is above 60"."""
    id: str
    never: str
    description: str = ""


class Catalog(BaseModel):
    connection_points: list[ConnectionPoint] = Field(default_factory=list)
    messages: list[Message] = Field(default_factory=list)
    actions: list[Action] = Field(default_factory=list)
    constraints: list[Constraint] = Field(default_factory=list)

    def signals(self) -> dict[str, Signal]:
        out: dict[str, Signal] = {}
        for cp in self.connection_points:
            out[cp.name] = Signal(
                name=cp.name, kind=cp.kind, direction=cp.direction, values=cp.values,
                min=cp.min, max=cp.max, resolution=cp.resolution, units=cp.units,
                initial=cp.initial, set_cost_ms=cp.set_cost_ms + cp.settle_ms,
            )
        for m in self.messages:
            for f in m.fields:
                name = f"{m.name}.{f.name}"
                out[name] = Signal(
                    name=name, kind=f.kind, direction=m.direction, values=f.values,
                    min=f.min, max=f.max, resolution=f.resolution, units=f.units,
                    initial=f.initial, set_cost_ms=m.send_cost_ms, source="message",
                    message=m.name, transport=m.transport,
                )
        return out

    def message(self, name: str) -> Optional[Message]:
        return next((m for m in self.messages if m.name == name), None)

    def action(self, name: str) -> Optional[Action]:
        return next((a for a in self.actions if a.name == name), None)


# --------------------------------------------------------------------------- requirements

Op = Literal["==", "!=", "<", "<=", ">", ">="]


class Condition(BaseModel):
    signal: str
    op: Op = "=="
    value: Value = True

    def holds(self, actual: Value) -> bool:
        return compare(actual, self.op, self.value)

    def key(self) -> str:
        return f"{self.signal}{self.op}{fmt_value(self.value)}"

    def __str__(self) -> str:
        return f"{self.signal} {self.op} {fmt_value(self.value)}"


class Timing(BaseModel):
    min_ms: Optional[float] = None
    max_ms: Optional[float] = None


class Pattern(str, Enum):
    ubiquitous = "ubiquitous"
    event = "event"        # When
    state = "state"        # While
    unwanted = "unwanted"  # If ... then
    optional = "optional"  # Where
    complex = "complex"    # While ... when


class Requirement(BaseModel):
    id: str
    text: str
    pattern: Optional[Pattern] = None
    trigger: Optional[Condition] = None
    conditions: list[Condition] = Field(default_factory=list)
    response: list[Condition] = Field(default_factory=list)
    timing: Optional[Timing] = None
    timing_ack: bool = False          # writer confirmed "no timing" is intentional
    strategy: Optional[str] = None    # per-requirement override of generation strategy
    parsed: bool = False
    parse_error: Optional[str] = None
    resolve_errors: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    def stimuli(self) -> list[Condition]:
        """All controllable variables of the requirement: conditions first, trigger last."""
        return list(self.conditions) + ([self.trigger] if self.trigger else [])


# --------------------------------------------------------------------------- project config


TargetKind = Literal["simulated", "partial", "hardware"]


class TargetConfig(BaseModel):
    """A named test-target configuration: which adapter implements each interface.

    adapters: {transport: {"type": "rig/file.py:Class" | "pkg.mod:Class", **constructor kwargs}}
      transports: "discrete" (connection points) plus each message's transport (mil1553, ...).
    start:  optional "file.py:function" called as fn(target, params) before the adapters are
            created (e.g. launch the software under test); may return a cleanup callable.
    params: default parameters passed to `start` (overridable per run).
    """
    name: str
    kind: TargetKind = "hardware"   # informational: simulated / partially simulated / hardware
    description: str = ""
    adapters: dict[str, dict[str, Any]] = Field(default_factory=dict)
    start: Optional[str] = None
    params: dict[str, Any] = Field(default_factory=dict)


class ProjectConfig(BaseModel):
    name: str = "untitled"
    default_timeout_ms: float = 1000.0     # used for responses with no timing bound
    negative_window_factor: float = 1.0    # observation window for negatives = max_ms * factor
    strategy: str = "mcdc"                 # all | mcdc | pairwise | random:N | positive_only
    max_cases_per_requirement: Optional[int] = None
    random_seed: int = 0
    disallowed_providers: list[str] = Field(default_factory=list)  # e.g. ["set:ENGINE_RUNNING"]
    targets: list[TargetConfig] = Field(default_factory=list)
    default_target: Optional[str] = None

    def target(self, name: Optional[str] = None) -> TargetConfig:
        name = name or self.default_target or (self.targets[0].name if self.targets else None)
        if not name:
            raise KeyError("no targets are configured for this workspace")
        for t in self.targets:
            if t.name.lower() == name.lower():
                return t
        raise KeyError(f"unknown target '{name}' (configured: {', '.join(t.name for t in self.targets) or 'none'})")


# --------------------------------------------------------------------------- tests

StepKind = Literal["set", "send", "action", "wait", "wait_until", "expect", "expect_not", "check"]
Phase = Literal["setup", "stimulus", "verify"]


class Step(BaseModel):
    kind: StepKind
    phase: Phase
    signal: Optional[str] = None
    op: Op = "=="
    value: Value = None
    message: Optional[str] = None
    fields: dict[str, Value] = Field(default_factory=dict)
    action: Optional[str] = None
    ms: Optional[float] = None          # wait duration / timeout / within / for
    est_ms: float = 0.0
    via: Optional[str] = None           # provider id that this step comes from (traceability)
    note: str = ""


class TestCase(BaseModel):
    id: str
    requirement_id: str
    title: str
    kind: Literal["positive", "negative"]
    assignment: dict[str, bool] = Field(default_factory=dict)  # condition key -> satisfied?
    steps: list[Step] = Field(default_factory=list)
    est_ms: float = 0.0
    traces: list[str] = Field(default_factory=list)  # all requirement ids used (incl. setup paths)
    warnings: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)   # informational, e.g. why a step is ordered


class Issue(BaseModel):
    level: Literal["error", "warning", "info"]
    code: str
    message: str
    requirement_id: Optional[str] = None


# --------------------------------------------------------------------------- helpers


def compare(actual: Value, op: str, expected: Value) -> bool:
    try:
        if op == "==":
            return actual == expected
        if op == "!=":
            return actual != expected
        if actual is None:
            return False
        if op == "<":
            return actual < expected
        if op == "<=":
            return actual <= expected
        if op == ">":
            return actual > expected
        if op == ">=":
            return actual >= expected
    except TypeError:
        return False
    raise ValueError(f"unknown operator {op}")


def fmt_value(v: Value) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)
