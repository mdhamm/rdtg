"""Which combinations of conditions can actually occur on the system under test.

A combination (e.g. "ENGINE_RUNNING is true and IGNITION_SW is OFF") is infeasible when:

  1. conditions on one signal contradict each other (THROTTLE <= 20 and THROTTLE >= 80);
  2. it contradicts the other requirements: every requirement is read as a rule "whenever its
     conditions and trigger hold, its response holds". Rules are chained (forward closure over
     the dependency graph), so A => B (R1) and B => C (R2) rules out A together with not-C;
  3. it matches a declared constraint in the catalog (things no requirement says: physics,
     wiring, interlocks).

Reading event-driven requirements as rules assumes the response persists while its conditions
hold; that is the same assumption the setup planner makes when it predicts the SUT's outputs.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .model import Condition, Constraint, Issue, Pattern, Requirement, Signal, fmt_value
from .parse import ParseError, parse_conditions, resolve_signal_name, resolve_value
from .project import Project

_NEG = {"==": "!=", "!=": "==", "<": ">=", ">=": "<", ">": "<=", "<=": ">"}
_SYM = {"==": "=", "!=": "≠", "<": "<", "<=": "≤", ">": ">", ">=": "≥"}


def _neg(c: Condition) -> Condition:
    return Condition(signal=c.signal, op=_NEG[c.op], value=c.value)


def fmt(c: Condition) -> str:
    return f"{c.signal} {_SYM[c.op]} {fmt_value(c.value)}"


def fmt_all(cs) -> str:
    return " and ".join(fmt(c) for c in cs)


@dataclass
class _Fact:
    cond: Condition
    because: Optional[str] = None   # None = part of the combination itself


@dataclass
class ParsedConstraint:
    constraint: Constraint
    conds: list[Condition]


class Feasibility:
    def __init__(self, project: Project):
        self.project = project
        self.signals: dict[str, Signal] = project.catalog.signals()
        self.rules: list[Requirement] = [
            r for r in project.requirements
            if r.parsed and not r.resolve_errors and r.response and r.pattern != Pattern.ubiquitous
        ]
        self.constraints: list[ParsedConstraint] = []
        self.constraint_errors: dict[str, str] = {}
        for k in project.catalog.constraints:
            try:
                self.constraints.append(ParsedConstraint(k, self._parse(k.never)))
            except (ParseError, ValueError) as e:
                self.constraint_errors[k.id] = str(e)
        self._cache: dict[tuple, Optional[str]] = {}

    def _parse(self, text: str) -> list[Condition]:
        out = []
        for c in parse_conditions(text):
            name = resolve_signal_name(c.signal, self.signals)
            if name is None:
                raise ValueError(f"unknown signal '{c.signal}'")
            out.append(Condition(signal=name, op=c.op, value=resolve_value(c.value, self.signals[name])))
        return out

    # ------------------------------------------------------------------ value reasoning

    def satisfiable(self, signal: str, conds: list[Condition]) -> bool:
        """Is there a value of `signal` satisfying every condition?"""
        sig = self.signals.get(signal)
        if sig is None:
            return True
        dom = sig.domain()
        if dom is not None:
            return any(all(c.holds(v) for c in conds) for v in dom)
        # analog: the boundaries of the conditions (at the signal's resolution) are the only
        # places the answer can change, so testing them is exhaustive at that resolution
        r = sig.resolution or 1.0
        pts = {p for c in conds if isinstance(c.value, (int, float)) and not isinstance(c.value, bool)
               for p in (c.value - r, c.value, c.value + r)}
        pts |= {v for v in (sig.min, sig.max) if v is not None}
        if not pts:
            return True
        pts = {p for p in pts if (sig.min is None or p >= sig.min) and (sig.max is None or p <= sig.max)}
        return any(all(c.holds(p) for c in conds) for p in pts)

    def _entailed(self, known: dict[str, list[_Fact]], c: Condition) -> bool:
        facts = [f.cond for f in known.get(c.signal, [])]
        return bool(facts) and not self.satisfiable(c.signal, facts + [_neg(c)])

    # ------------------------------------------------------------------ the check

    def check(self, goals: list[Condition]) -> Optional[str]:
        """None if the combination can occur, else a human-readable reason it cannot."""
        key = tuple(sorted(g.key() for g in goals))
        if key not in self._cache:
            self._cache[key] = self._check(goals)
        return self._cache[key]

    def _check(self, goals: list[Condition]) -> Optional[str]:
        known: dict[str, list[_Fact]] = {}
        for g in goals:
            known.setdefault(g.signal, []).append(_Fact(g))
        for sig, facts in known.items():
            if not self.satisfiable(sig, [f.cond for f in facts]):
                return f"{fmt_all(f.cond for f in facts)} cannot hold at the same time"

        for _ in range(len(self.rules) + 2):
            changed = False
            for k in self.constraints:
                if all(self._entailed(known, c) for c in k.conds):
                    desc = f" ({k.constraint.description})" if k.constraint.description else ""
                    return f"constraint {k.constraint.id}: {fmt_all(k.conds)} can never happen{desc}"
            for r in self.rules:
                stimuli = r.stimuli()
                if not all(self._entailed(known, s) for s in stimuli):
                    continue
                for c in r.response:
                    if c.op != "==" or self._entailed(known, c):
                        continue
                    existing = known.get(c.signal, [])
                    if not self.satisfiable(c.signal, [f.cond for f in existing] + [c]):
                        clash = existing[0]
                        why = f"{r.id} sets {fmt(c)} whenever {fmt_all(stimuli)}"
                        # derived facts the rule fired on (not unrelated derivations)
                        chain = [f.because for s in stimuli for f in known.get(s.signal, []) if f.because]
                        via = f" (given {'; '.join(dict.fromkeys(chain))})" if chain else ""
                        need = fmt(clash.cond) + (f", which follows from {clash.because}" if clash.because else "")
                        return f"{why}{via}, but the combination needs {need}"
                    known.setdefault(c.signal, []).append(_Fact(c, f"{r.id}: {fmt_all(stimuli)} ⇒ {fmt(c)}"))
                    changed = True
            if not changed:
                break
        return None

    # ------------------------------------------------------------------ lint

    def issues(self) -> list[Issue]:
        out = [Issue(level="error", code="BAD_CONSTRAINT", message=f"constraint {cid}: {err}")
               for cid, err in self.constraint_errors.items()]
        for r in self.rules:
            stimuli = r.stimuli()
            reason = self.check(stimuli)
            if reason:
                out.append(Issue(level="error", code="NEVER_TRIGGERED", requirement_id=r.id,
                                 message=f"the requirement's conditions can never all hold: {reason}"))
                continue
            # A condition that can't be false while the others are true never changes the outcome.
            for i, c in enumerate(stimuli):
                flipped = [_neg(s) if j == i else s for j, s in enumerate(stimuli)]
                why = self.check(flipped)
                if why:
                    out.append(Issue(level="warning", code="REDUNDANT_CONDITION", requirement_id=r.id,
                                     message=f"'{fmt(c)}' can never be false while the other conditions "
                                             f"hold, so it has no independent effect: {why}"))
        return out
