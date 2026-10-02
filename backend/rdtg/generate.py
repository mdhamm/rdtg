"""Test case generation from requirements + dependency graph.

For each requirement the controllable variables are its conditions plus its trigger. A strategy
chooses which true/false assignments to test. Each assignment becomes a test case:

  setup     establish every condition (or its violation), put the trigger in its inactive state,
            and make sure the expected response is NOT already present (otherwise the test would
            pass vacuously). Uses the cheapest path through the dependency graph.
  stimulus  apply the trigger (positive) or drive it to its nearest violating value (negative).
  verify    positive: response appears within max_ms (and not before min_ms)
            negative: response does not appear within the observation window
"""
from __future__ import annotations

import itertools
import random
from dataclasses import dataclass, field
from typing import Callable, Optional

from .feasibility import Feasibility, fmt
from .graph import DependencyGraph, negate, witness
from .model import (Condition, Direction, Issue, Pattern, Requirement, Step, TestCase,
                    fmt_value)
from .project import Project

STRATEGIES = ("all", "mcdc", "pairwise", "random:N", "positive_only")

# Prefixes of reasons for combinations that exist but still can't be tested meaningfully.
OUTCOME_FORCED = "the response would occur anyway"
ALREADY_PRESENT = "the response is already present before the trigger"
_POSITIVE = "positive case"


# --------------------------------------------------------------------------- strategies


def assignments(n: int, strategy: str, seed: int = 0) -> list[tuple[bool, ...]]:
    """Truth assignments for n AND-ed variables. The all-true (positive) case is always first."""
    pos = tuple([True] * n)
    if n == 0 or strategy == "positive_only":
        return [pos]
    if strategy == "all":
        rows = list(itertools.product([True, False], repeat=n))
    elif strategy in ("mcdc", "one_negative"):
        # For an AND of n conditions, MC/DC = all-true + each condition independently false.
        rows = [pos] + [tuple(j != i for j in range(n)) for i in range(n)]
    elif strategy == "pairwise":
        rows = _pairwise(n)
    elif strategy.startswith("random"):
        k = int(strategy.split(":", 1)[1]) if ":" in strategy else n + 1
        universe = [r for r in itertools.product([True, False], repeat=n) if r != pos]
        rng = random.Random(seed)
        rows = [pos] + rng.sample(universe, min(max(k - 1, 0), len(universe)))
    else:
        raise ValueError(f"unknown strategy '{strategy}' (expected one of {STRATEGIES})")
    rows = [pos] + [r for r in rows if r != pos]
    return list(dict.fromkeys(rows))


def _pairwise(n: int) -> list[tuple[bool, ...]]:
    if n <= 2:
        return list(itertools.product([True, False], repeat=n))
    need = {(i, a, j, b) for i, j in itertools.combinations(range(n), 2)
            for a in (True, False) for b in (True, False)}
    rows: list[tuple[bool, ...]] = []

    def cover(row):
        return {(i, row[i], j, row[j]) for i, j in itertools.combinations(range(n), 2)}

    rows.append(tuple([True] * n))
    need -= cover(rows[0])
    while need:
        i, a, j, b = min(need)
        row: list[Optional[bool]] = [None] * n
        row[i], row[j] = a, b
        for k in range(n):
            if row[k] is not None:
                continue
            best_v, best_gain = True, -1
            for v in (True, False):
                row[k] = v
                gain = sum(1 for (p, pa, q, qb) in need
                           if row[p] is not None and row[q] is not None and row[p] == pa and row[q] == qb)
                if gain > best_gain:
                    best_v, best_gain = v, gain
            row[k] = best_v
        t = tuple(row)  # type: ignore[arg-type]
        rows.append(t)
        need -= cover(t)
    return rows


Row = tuple[bool, ...]
_MAX_ENUMERATE = 12   # up to 2^12 combinations are checked exhaustively


def select_rows(n: int, strategy: str, seed: int,
                infeasible: Callable[[Row], Optional[str]]) -> tuple[list[Row], list[dict]]:
    """Like `assignments`, but only returns combinations that can occur.

    Returns (rows, skipped). skipped entries: {"row": {index: bool}, "reason": str, "note": str}
    - all / mcdc: each wanted-but-impossible combination
    - pairwise / random: drawn from feasible combinations only; value pairs that occur in no
      feasible combination are reported
    The positive (all-true) row comes first if it is feasible."""
    pos: Row = tuple([True] * n)
    skipped: list[dict] = []

    def skip(row: Row, reason: str, note: str = "", idx=None):
        idx = range(n) if idx is None else idx
        skipped.append({"row": {i: row[i] for i in idx}, "reason": reason, "note": note})

    if n == 0 or strategy == "positive_only" or strategy in ("all", "mcdc", "one_negative"):
        wanted = assignments(n, "positive_only" if n == 0 else strategy, seed)
        rows = []
        for i, row in enumerate(wanted):
            why = infeasible(row)
            if why is None:
                rows.append(row)
                continue
            note = ""
            if row == pos:
                note = _POSITIVE
            elif strategy in ("mcdc", "one_negative") and sum(not v for v in row) == 1:
                k = row.index(False)
                note = f"independent effect of condition {k + 1} cannot be shown"
            skip(row, why, note)
        return rows, skipped

    if n > _MAX_ENUMERATE:  # too many to enumerate: filter the strategy's own rows
        rows = []
        for row in assignments(n, strategy, seed):
            why = infeasible(row)
            if why is None:
                rows.append(row)
            elif row == pos:
                skip(row, why, _POSITIVE)
        return rows, skipped

    universe = list(itertools.product([True, False], repeat=n))
    reasons = {row: infeasible(row) for row in universe}
    feasible = [r for r in universe if reasons[r] is None]
    if reasons[pos]:
        skip(pos, reasons[pos], _POSITIVE)

    if strategy.startswith("random"):
        k = int(strategy.split(":", 1)[1]) if ":" in strategy else n + 1
        others = [r for r in feasible if r != pos]
        picked = random.Random(seed).sample(others, min(max(k - (pos in feasible), 0), len(others)))
        return ([pos] if pos in feasible else []) + picked, skipped

    if strategy == "pairwise":
        pairs = lambda r: {(i, r[i], j, r[j]) for i, j in itertools.combinations(range(n), 2)}
        coverable = set().union(*(pairs(r) for r in feasible)) if feasible else set()
        rows = [pos] if pos in feasible else []
        need = coverable - (pairs(pos) if rows else set())
        while need:
            best = max(feasible, key=lambda r: len(pairs(r) & need))
            rows.append(best)
            need -= pairs(best)
        for i, j in itertools.combinations(range(n), 2):
            for a in (True, False):
                for b in (True, False):
                    if (i, a, j, b) not in coverable:
                        example = next(r for r in universe if r[i] == a and r[j] == b)
                        skip(example, reasons[example] or "no feasible combination", "value pair never possible",
                             idx=(i, j))
        return rows, skipped

    raise ValueError(f"unknown strategy '{strategy}' (expected one of {STRATEGIES})")


# --------------------------------------------------------------------------- generation


@dataclass
class Suite:
    tests: list[TestCase]
    issues: list[Issue] = field(default_factory=list)
    # combinations the strategy asked for that cannot occur:
    # {requirement_id, assignment: {condition: bool}, reason, note}
    untestable: list[dict] = field(default_factory=list)

    @property
    def est_ms(self) -> float:
        return sum(t.est_ms for t in self.tests)

    def for_requirement(self, req_id: str) -> list[TestCase]:
        return [t for t in self.tests if t.requirement_id == req_id]


class Generator:
    def __init__(self, project: Project, graph: Optional[DependencyGraph] = None,
                 strategy: Optional[str] = None, max_cases: Optional[int] = None):
        self.project = project
        self.cfg = project.config
        self.graph = graph or DependencyGraph(project)
        self.signals = self.graph.signals
        self.feasibility = Feasibility(project)
        self.strategy_override = strategy
        self.max_cases = max_cases if max_cases is not None else self.cfg.max_cases_per_requirement

    def strategy_for(self, req: Requirement) -> str:
        return self.strategy_override or req.strategy or self.cfg.strategy

    def run(self, requirement_ids: Optional[list[str]] = None) -> Suite:
        suite = Suite(tests=[])
        for req in self.project.requirements:
            if requirement_ids and req.id not in requirement_ids:
                continue
            if not req.parsed or req.resolve_errors or not req.response:
                suite.issues.append(Issue(level="error", code="SKIPPED", requirement_id=req.id,
                                          message="not generated: requirement has parse/resolve errors"))
                continue
            tests, untestable = self.plan_requirement(req)
            suite.tests.extend(tests)
            suite.untestable.extend(untestable)
            for u in untestable:
                if u["note"].startswith(_POSITIVE):
                    suite.issues.append(Issue(level="error", code="NO_POSITIVE_TEST", requirement_id=req.id,
                                              message=f"{u['note']}: {u['reason']}"))
        return suite

    def for_requirement(self, req: Requirement) -> list[TestCase]:
        return self.plan_requirement(req)[0]

    def follows_trigger(self, conditions: list[Condition], trigger: Condition) -> list[Condition]:
        """Output conditions that can't hold while the trigger is inactive: per the other
        requirements they are consequences of the trigger (e.g. a pump command that follows the
        ignition switch), so a test must not try to set them up before the stimulus."""
        out = []
        for c in conditions:
            s = self.signals.get(c.signal)
            if s and s.direction == Direction.output and self.feasibility.check([c, negate(trigger)]):
                out.append(c)
        return out

    def plan_requirement(self, req: Requirement) -> tuple[list[TestCase], list[dict]]:
        variables = req.stimuli()

        resp_off = [negate(c) for c in req.response]

        def infeasible(row) -> Optional[str]:
            goals = [v if a else negate(v) for v, a in zip(variables, row)]
            why = self.feasibility.check(goals)
            if why:
                return why
            if not all(row):
                # Negative case expects "no response". If the other requirements produce the
                # response anyway under these conditions, the expectation itself is wrong.
                why = self.feasibility.check(goals + resp_off)
                if why:
                    return f"{OUTCOME_FORCED}: {why}"
            elif variables:
                # Positive case starts with the trigger inactive and the response absent; if the
                # other requirements force the response on there, the stimulus proves nothing.
                # Conditions produced by the trigger itself are not part of that starting state.
                before = [g for g in goals[:-1] if g not in self.follows_trigger(goals[:-1], variables[-1])]
                why = self.feasibility.check(before + [negate(variables[-1])] + resp_off)
                if why:
                    return f"{ALREADY_PRESENT}: {why}"
            return None

        rows, skipped = select_rows(len(variables), self.strategy_for(req), self.cfg.random_seed, infeasible)
        if self.max_cases:
            rows = rows[: self.max_cases]
        tests = [self._build(req, variables, row, i + 1) for i, row in enumerate(rows)]
        untestable = []
        for sk in skipped:
            assignment = {str(variables[i]): v for i, v in sk["row"].items()}
            note, reason = sk["note"], sk["reason"]
            if note.startswith("independent effect of condition"):
                k = int(note.split("condition ")[1].split(" ")[0]) - 1
                note = f"independent effect of {fmt(variables[k])} cannot be shown"
            if note == _POSITIVE:
                note = ("positive case: the trigger can never fire from a state without the response"
                        if reason.startswith(ALREADY_PRESENT)
                        else "positive case: the requirement can never be triggered")
            elif reason.startswith(OUTCOME_FORCED):
                extra = "other requirements produce the response under these conditions"
                note = f"{note}; {extra}" if note else extra
            untestable.append({"requirement_id": req.id, "assignment": assignment,
                               "reason": reason, "note": note})
        return tests, untestable

    # ------------------------------------------------------------------ one test case

    def _build(self, req: Requirement, variables: list[Condition], row: tuple[bool, ...],
               idx: int) -> TestCase:
        positive = all(row)
        tc = TestCase(id=f"TC-{req.id}-{idx:02d}", requirement_id=req.id,
                      kind="positive" if positive else "negative", title="",
                      assignment={str(v): a for v, a in zip(variables, row)})
        violated = [str(v) for v, a in zip(variables, row) if not a]
        tc.title = (f"{req.id} positive: all conditions met" if positive
                    else f"{req.id} negative: {', '.join(violated)} not met")

        stim: Optional[Condition] = variables[-1] if variables else None
        stim_on = row[-1] if variables else True
        others = [v if a else negate(v) for v, a in zip(variables[:-1], row[:-1])]

        # ---- setup
        follows = self.follows_trigger(others, stim) if stim is not None and stim_on else []
        for f in follows:
            tc.notes.append(f"{fmt(f)} is produced by the trigger ({self.feasibility.check([f, negate(stim)])}), "
                            f"so it is checked after the stimulus instead of being set up beforehand")
        others = [g for g in others if g not in follows]
        resp_off = [negate(c) for c in req.response]
        setup_goals = others + resp_off + ([negate(stim)] if stim else [])
        plan = self.graph.plan(setup_goals, keep_last=False)
        if not plan.ok:
            fallback = self.graph.plan(others + ([negate(stim)] if stim else []), keep_last=False)
            if fallback.ok or not plan.steps:
                tc.warnings.append("could not clear the expected response before the stimulus; "
                                   "the test may pass vacuously")
                plan = fallback
        tc.warnings += plan.warnings
        steps = list(plan.steps)
        steps = self._reassert_inputs(others + ([negate(stim)] if stim else []), steps)
        for g in others:
            s = self.signals.get(g.signal)
            if s and s.direction == Direction.output:
                steps.append(Step(kind="check", phase="setup", signal=g.signal, op=g.op, value=g.value,
                                  note="precondition"))
        # Positive cases: a response that is already present means the stimulus can't be shown to
        # cause it -> inconclusive. Negative cases skip this check so that a response present
        # without its trigger fails the expect_not verification instead.
        if positive:
            for c in req.response:
                n = negate(c)
                steps.append(Step(kind="check", phase="setup", signal=n.signal, op=n.op, value=n.value,
                                  note="response must not already be present"))
        state = plan.state

        # ---- stimulus
        if stim is not None:
            target = stim if stim_on else negate(stim)
            sig = self.signals.get(stim.signal)
            if sig and sig.direction == Direction.input:
                value = witness(target, sig)
                steps.append(self._drive(stim.signal, value, "stimulus",
                                         "trigger" if stim_on else "near-miss (boundary) value"))
                state[stim.signal] = value
                self.graph.propagate(state)
            elif stim_on:
                res = self.graph.plan([stim], state)
                for s in res.steps:
                    s.phase = "stimulus"
                steps += res.steps
                tc.warnings += res.warnings
                state = res.state

        # ---- verify
        t_max = self.graph.response_time(req)
        t_min = req.timing.min_ms if req.timing and req.timing.min_ms else None
        window = t_max * self.cfg.negative_window_factor
        for f in follows:
            steps.append(Step(kind="expect", phase="verify", signal=f.signal, op=f.op, value=f.value,
                              ms=t_max, note="condition produced by the trigger"))
        # All verify times are measured from the stimulus, so only the first response adds to the
        # estimated duration; later responses are observed over the same window.
        for i, c in enumerate(req.response):
            first = i == 0
            if positive:
                if t_min:
                    steps.append(Step(kind="expect_not", phase="verify", signal=c.signal, op=c.op,
                                      value=c.value, ms=t_min, est_ms=t_min if first else 0,
                                      note=f"must not respond earlier than {t_min:g} ms"))
                steps.append(Step(kind="expect", phase="verify", signal=c.signal, op=c.op,
                                  value=c.value, ms=t_max,
                                  est_ms=(t_max - (t_min or 0)) if first else 0,
                                  note=f"within {t_max:g} ms" + ("" if req.timing and req.timing.max_ms
                                                                 else " (default timeout)")))
            else:
                steps.append(Step(kind="expect_not", phase="verify", signal=c.signal, op=c.op,
                                  value=c.value, ms=window, est_ms=window if first else 0,
                                  note=f"no response within {window:g} ms"))

        tc.steps = steps
        tc.est_ms = sum(s.est_ms for s in steps)
        tc.traces = [req.id] + sorted({s.via[4:] for s in steps if s.via and s.via.startswith("req:")})
        return tc

    def _drive(self, signal: str, value, phase: str, note: str) -> Step:
        sig = self.signals[signal]
        if sig.source == "message":
            return Step(kind="send", phase=phase, message=sig.message, fields={signal.split(".", 1)[1]: value},
                        signal=signal, value=value, est_ms=sig.set_cost_ms, via=f"send:{signal}", note=note)
        return Step(kind="set", phase=phase, signal=signal, value=value, est_ms=sig.set_cost_ms,
                    via=f"set:{signal}", note=note)

    def _reassert_inputs(self, goals: list[Condition], steps: list[Step]) -> list[Step]:
        """Explicitly drive every input precondition, even if it already holds after a reset —
        tests should not silently depend on rig defaults."""
        driven = {s.signal for s in steps if s.kind in ("set", "send")}
        extra = []
        for g in goals:
            sig = self.signals.get(g.signal)
            if sig and sig.direction == Direction.input and g.signal not in driven:
                extra.append(self._drive(g.signal, witness(g, sig), "setup", "precondition"))
                driven.add(g.signal)
        return extra + steps


def describe_condition(c: Condition) -> str:
    words = {"==": "=", "!=": "≠", "<": "<", "<=": "≤", ">": ">", ">=": "≥"}
    return f"{c.signal} {words[c.op]} {fmt_value(c.value)}"
