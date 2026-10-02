"""Dependency graph between requirements, the interface catalog, and test setup planning.

Facts (conditions such as ENGINE_RUNNING == true) are produced by *providers*:

    set:<CP>          drive an input connection point     cost = set_cost_ms + settle_ms
    send:<MSG>.<fld>  inject an ICD message field          cost = send_cost_ms
    action:<name>     run a catalog action                 cost = action.cost_ms
    req:<id>          rely on another requirement (SUT)    cost = its max timing (or default)

A provider may itself require facts (a requirement needs its trigger + conditions), so this is an
AND/OR graph: a fact costs the cheapest provider (OR); a provider costs its own weight plus the
cost of all its requirements (AND). Costs are solved with a Bellman-Ford style fixpoint so cycles
between requirements are handled.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import Optional

from .model import (Condition, Direction, Pattern, Requirement, Signal, SignalKind, Step,
                    Value, compare, fmt_value)
from .project import Project

INF = math.inf
_NEG = {"==": "!=", "!=": "==", "<": ">=", ">=": "<", ">": "<=", "<=": ">"}


def negate(c: Condition) -> Condition:
    return Condition(signal=c.signal, op=_NEG[c.op], value=c.value)


def witness(c: Condition, sig: Signal) -> Optional[Value]:
    """A concrete value of `sig` that satisfies `c`, preferring boundary values."""
    if c.op == "==":
        return c.value
    dom = sig.domain()
    if dom is not None:
        return next((v for v in dom if compare(v, c.op, c.value)), None)
    r = sig.resolution or 1.0
    v = {"!=": c.value + r if isinstance(c.value, (int, float)) else None,
         ">": c.value + r, ">=": c.value, "<": c.value - r, "<=": c.value}[c.op]
    if v is None:
        return None
    if (sig.min is not None and v < sig.min) or (sig.max is not None and v > sig.max):
        return None
    if isinstance(v, float) and v.is_integer() and isinstance(c.value, int):
        v = int(v)
    return v


@dataclass
class Provider:
    id: str
    kind: str                        # set | send | action | requirement
    cost: float
    requires: list[Condition] = field(default_factory=list)
    produces: list[Condition] = field(default_factory=list)  # fixed facts
    free_signal: Optional[str] = None                         # set/send: any value of this signal
    requirement: Optional[Requirement] = None
    label: str = ""

    def can_produce(self, c: Condition, signals: dict[str, Signal]) -> Optional[Value]:
        if self.free_signal == c.signal:
            return witness(c, signals[c.signal])
        for p in self.produces:
            if p.signal == c.signal and p.op == "==" and c.holds(p.value):
                return p.value
        return None


@dataclass
class PlanResult:
    steps: list[Step]
    cost: float
    state: dict[str, Value]
    used: list[str]
    warnings: list[str]
    ok: bool


class DependencyGraph:
    def __init__(self, project: Project):
        self.project = project
        self.config = project.config
        self.signals = project.catalog.signals()
        self.requirements = [r for r in project.requirements if r.parsed and not r.resolve_errors]
        self.providers: list[Provider] = []
        self._build()
        self._costs: dict[str, float] = {}
        self._solve()

    # ------------------------------------------------------------------ construction

    def initial_state(self) -> dict[str, Value]:
        return {name: s.default_initial() for name, s in self.signals.items()}

    def _build(self) -> None:
        disallowed = set(self.config.disallowed_providers)
        for name, s in self.signals.items():
            if s.direction != Direction.input:
                continue
            if s.source == "connection_point":
                p = Provider(id=f"set:{name}", kind="set", cost=s.set_cost_ms, free_signal=name,
                             label=f"set {name}")
            else:
                p = Provider(id=f"send:{name}", kind="send", cost=s.set_cost_ms, free_signal=name,
                             label=f"send {s.message} ({s.transport})")
            if p.id not in disallowed:
                self.providers.append(p)
        for a in self.project.catalog.actions:
            p = Provider(id=f"action:{a.name}", kind="action", cost=a.cost_ms,
                         requires=[Condition(signal=k, value=v) for k, v in a.requires.items()],
                         produces=[Condition(signal=k, value=v) for k, v in a.produces.items()],
                         label=f"action {a.name}")
            if p.id not in disallowed:
                self.providers.append(p)
        for r in self.requirements:
            if r.pattern == Pattern.ubiquitous or not r.response:
                continue
            p = Provider(id=f"req:{r.id}", kind="requirement", cost=self.response_time(r),
                         requires=r.stimuli(),
                         produces=[c for c in r.response if c.op == "=="],
                         requirement=r, label=r.id)
            if p.id not in disallowed:
                self.providers.append(p)

    def response_time(self, r: Requirement) -> float:
        if r.timing and r.timing.max_ms:
            return r.timing.max_ms
        return self.config.default_timeout_ms

    # ------------------------------------------------------------------ cost solver

    def _nodes(self) -> list[Condition]:
        seen: dict[str, Condition] = {}
        for p in self.providers:
            for c in p.requires:
                seen.setdefault(c.key(), c)
                n = negate(c)
                seen.setdefault(n.key(), n)
        return list(seen.values())

    def _step_cost(self, c: Condition, init: dict[str, Value]) -> float:
        if c.signal in init and c.holds(init[c.signal]):
            return 0.0
        best = INF
        for p in self.providers:
            if p.can_produce(c, self.signals) is None:
                continue
            total = p.cost + sum(self._costs.get(r.key(), INF) if r.key() in self._costs
                                 else self._step_cost_leaf(r, init) for r in p.requires)
            best = min(best, total)
        return best

    def _step_cost_leaf(self, c: Condition, init) -> float:
        return 0.0 if c.signal in init and c.holds(init[c.signal]) else INF

    def _solve(self) -> None:
        init = self.initial_state()
        nodes = self._nodes()
        self._costs = {n.key(): INF for n in nodes}
        for _ in range(len(nodes) + 2):
            changed = False
            for n in nodes:
                v = self._step_cost(n, init)
                if v < self._costs[n.key()]:
                    self._costs[n.key()] = v
                    changed = True
            if not changed:
                break

    def cost(self, c: Condition) -> float:
        if c.key() in self._costs:
            return self._costs[c.key()]
        return self._step_cost(c, self.initial_state())

    def providers_for(self, c: Condition) -> list[tuple[Provider, float]]:
        out = []
        for p in self.providers:
            if p.can_produce(c, self.signals) is not None:
                out.append((p, p.cost + sum(self.cost(r) for r in p.requires)))
        return sorted(out, key=lambda t: t[1])

    # ------------------------------------------------------------------ SUT prediction

    def propagate(self, state: dict[str, Value]) -> None:
        """Predict SUT outputs: apply every requirement whose stimuli currently hold."""
        for _ in range(len(self.requirements) + 1):
            changed = False
            for r in self.requirements:
                if r.pattern == Pattern.ubiquitous:
                    continue
                if all(c.signal in state and c.holds(state[c.signal]) for c in r.stimuli()):
                    for c in r.response:
                        if c.op == "==" and state.get(c.signal) != c.value:
                            state[c.signal] = c.value
                            changed = True
            if not changed:
                return

    # ------------------------------------------------------------------ planning

    def plan(self, goals: list[Condition], state: Optional[dict[str, Value]] = None,
             keep_last: bool = True) -> PlanResult:
        """Find a cheap ordered step sequence that makes all goals hold simultaneously.

        Goals are attempted in the given order; if a later goal undoes an earlier one, other
        orderings are tried (the last goal stays last when keep_last — it is the trigger)."""
        base = dict(state) if state is not None else self.initial_state()
        self.propagate(base)
        head, tail = (goals[:-1], goals[-1:]) if keep_last and goals else (goals, [])
        best: Optional[PlanResult] = None
        for i, perm in enumerate(itertools.permutations(head)):
            if i >= 120:
                break
            res = self._plan_ordered(list(perm) + tail, dict(base))
            if res.ok and (best is None or not best.ok or res.cost < best.cost):
                best = res
                if i == 0:
                    break
            elif best is None:
                best = res
        assert best is not None or not goals
        if best is None:
            return PlanResult([], 0.0, base, [], [], True)
        return best

    def _plan_ordered(self, goals: list[Condition], state: dict[str, Value]) -> PlanResult:
        steps: list[Step] = []
        used: list[str] = []
        warnings: list[str] = []
        for g in goals:
            self._achieve(g, state, steps, used, warnings, stack=frozenset())
        missing = [g for g in goals if not (g.signal in state and g.holds(state[g.signal]))]
        for g in missing:
            warnings.append(f"could not establish {g} together with the other conditions")
        return PlanResult(steps, sum(s.est_ms for s in steps), state, used, warnings, not missing)

    def _achieve(self, g: Condition, state, steps, used, warnings, stack: frozenset) -> bool:
        if g.signal in state and g.holds(state[g.signal]):
            return True
        if g.key() in stack:
            return False
        stack = stack | {g.key()}
        for p, _ in self.providers_for(g):
            if p.kind == "requirement" and p.id in {s for s in stack if s.startswith("req:")}:
                continue
            if self._apply(p, g, state, steps, used, warnings, stack | {p.id}):
                return True
        warnings.append(f"no way to establish {g} (unreachable from rig inputs)")
        return False

    def _apply(self, p: Provider, g: Condition, state, steps, used, warnings, stack) -> bool:
        snapshot = (dict(state), len(steps), len(used), len(warnings))
        def rollback():
            state.clear(); state.update(snapshot[0])
            del steps[snapshot[1]:]; del used[snapshot[2]:]; del warnings[snapshot[3]:]

        if p.kind in ("set", "send"):
            value = p.can_produce(g, self.signals)
            sig = self.signals[g.signal]
            if p.kind == "set":
                steps.append(Step(kind="set", phase="setup", signal=g.signal, value=value,
                                  est_ms=p.cost, via=p.id))
            else:
                fld = g.signal.split(".", 1)[1]
                steps.append(Step(kind="send", phase="setup", message=sig.message,
                                  fields={fld: value}, signal=g.signal, value=value,
                                  est_ms=p.cost, via=p.id))
            state[g.signal] = value
        elif p.kind == "action":
            for r in p.requires:
                if not self._achieve(r, state, steps, used, warnings, stack):
                    rollback(); return False
            steps.append(Step(kind="action", phase="setup", action=p.id.split(":", 1)[1],
                              est_ms=p.cost, via=p.id, note=p.label))
            for c in p.produces:
                state[c.signal] = c.value
        else:  # requirement
            r = p.requirement
            for c in r.conditions:
                if not self._achieve(c, state, steps, used, warnings, stack):
                    rollback(); return False
            if r.trigger:
                trig = r.trigger
                if trig.signal in state and trig.holds(state[trig.signal]):
                    # event-driven: need an edge, so drive the trigger inactive first
                    if not self._achieve(negate(trig), state, steps, used, warnings, stack):
                        rollback(); return False
                if not self._achieve(trig, state, steps, used, warnings, stack):
                    rollback(); return False
            t = self.response_time(r)
            for c in p.produces:
                steps.append(Step(kind="wait_until", phase="setup", signal=c.signal, op="==",
                                  value=c.value, ms=t, est_ms=t, via=p.id,
                                  note=f"per {r.id}"))
                state[c.signal] = c.value
        used.append(p.id)
        self.propagate(state)
        if not (g.signal in state and g.holds(state[g.signal])):
            rollback()
            return False
        return True

    # ------------------------------------------------------------------ export

    def to_dict(self) -> dict:
        """Bipartite graph (facts <-> providers) for visualization."""
        nodes: dict[str, dict] = {}
        edges: list[dict] = []

        def fact(c: Condition) -> str:
            nid = "f:" + c.key()
            if nid not in nodes:
                cost = self.cost(c)
                nodes[nid] = {"id": nid, "type": "fact", "label": f"{c.signal} {c.op} {fmt_value(c.value)}",
                              "signal": c.signal, "cost": None if cost == INF else cost,
                              "reachable": cost != INF}
            return nid

        for p in self.providers:
            if p.kind in ("set", "send"):
                continue  # implicit: every input signal is directly settable
            pid = "p:" + p.id
            nodes[pid] = {"id": pid, "type": p.kind, "label": p.label, "cost": p.cost}
            for r in p.requires:
                edges.append({"source": fact(r), "target": pid, "kind": "requires", "weight": 0})
            for c in p.produces:
                edges.append({"source": pid, "target": fact(c), "kind": "produces", "weight": p.cost})
        # direct-drive edges for facts that appear in the graph
        for nid, n in list(nodes.items()):
            if n["type"] != "fact":
                continue
            s = self.signals.get(n["signal"])
            if s and s.direction == Direction.input:
                pid = f"p:{'set' if s.source == 'connection_point' else 'send'}:{s.name}"
                if pid not in nodes:
                    nodes[pid] = {"id": pid, "type": "set" if s.source == "connection_point" else "send",
                                  "label": f"rig: {s.name}", "cost": s.set_cost_ms}
                edges.append({"source": pid, "target": nid, "kind": "produces", "weight": s.set_cost_ms})
        return {"nodes": list(nodes.values()), "edges": edges}

    def to_dot(self) -> str:
        d = self.to_dict()
        shape = {"fact": "ellipse", "requirement": "box", "action": "component",
                 "set": "cds", "send": "cds"}
        lines = ["digraph rdtg {", "  rankdir=LR;", '  node [fontname="Helvetica"];']
        for n in d["nodes"]:
            extra = ', style=dashed, color=red' if n["type"] == "fact" and not n.get("reachable", True) else ""
            lines.append(f'  "{n["id"]}" [label="{n["label"]}", shape={shape.get(n["type"], "box")}{extra}];')
        for e in d["edges"]:
            lbl = f' [label="{e["weight"]:g} ms"]' if e["kind"] == "produces" else " [style=dotted]"
            lines.append(f'  "{e["source"]}" -> "{e["target"]}"{lbl};')
        lines.append("}")
        return "\n".join(lines)

    def issues(self) -> list:
        from .model import Issue
        out = []
        for r in self.requirements:
            for c in r.stimuli():
                for variant, label in ((c, "satisfy"), (negate(c), "violate")):
                    if self.cost(variant) == INF:
                        out.append(Issue(level="warning" if label == "violate" else "error",
                                         code="UNREACHABLE", requirement_id=r.id,
                                         message=f"cannot {label} '{c}': no rig input, action or "
                                                 f"requirement produces {variant}"))
        from .feasibility import Feasibility
        return out + Feasibility(self.project).issues()
