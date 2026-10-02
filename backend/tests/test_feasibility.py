from rdtg.feasibility import Feasibility
from rdtg.generate import Generator, select_rows
from rdtg.graph import DependencyGraph
from rdtg.model import Condition, Constraint
from rdtg.project import build_requirement, resolve_all


def C(signal, value=True, op="=="):
    return Condition(signal=signal, op=op, value=value)


def test_same_signal_contradiction(demo):
    f = Feasibility(demo)
    assert "cannot hold at the same time" in f.check([C("THROTTLE", 20, "<="), C("THROTTLE", 80, ">=")])
    assert f.check([C("THROTTLE", 20, ">"), C("THROTTLE", 80, "<")]) is None
    assert f.check([C("IGNITION_SW", "ON"), C("IGNITION_SW", "OFF", "!=")]) is None


def test_other_requirement_rules_out_combination(demo):
    """SRS-005: IGNITION OFF => ENGINE_RUNNING false, so 'running with ignition off' is impossible."""
    reason = Feasibility(demo).check([C("ENGINE_RUNNING"), C("IGNITION_SW", "OFF")])
    assert "SRS-005" in reason and "ENGINE_RUNNING = true" in reason


def test_rules_chain_through_dependency_graph(demo):
    """SRS-001 gives ENGINE_RUNNING, which with THROTTLE > 20 gives rpm_valid (SRS-002)."""
    reason = Feasibility(demo).check([C("FUEL_PUMP_OK"), C("IGNITION_SW", "ON"), C("THROTTLE", 50, ">"),
                                      C("ENG_STATUS.rpm_valid", False)])
    assert "SRS-002" in reason and "given SRS-001" in reason


def test_declared_constraint(demo):
    f = Feasibility(demo)
    reason = f.check([C("FUEL_PUMP_OK", False), C("FUEL_PRESSURE", 70, ">")])
    assert reason and "PHYS-1" in reason
    assert f.check([C("FUEL_PUMP_OK", False), C("FUEL_PRESSURE", 50, ">")]) is None  # 51..60 allowed


def test_bad_constraint_is_reported(demo):
    demo.catalog.constraints.append(Constraint(id="X", never="NOPE is true"))
    assert any(i.code == "BAD_CONSTRAINT" for i in Feasibility(demo).issues())


def test_strategies_skip_impossible_combinations(demo):
    """SRS-007: While ENGINE_RUNNING and IGNITION_SW ON, when THROTTLE > 95 ... (vars: E, I, T)."""
    req = demo.requirement("SRS-007")
    all_ = Generator(demo, strategy="all")
    tests, skipped = all_.plan_requirement(req)
    assert len(tests) == 6 and len(skipped) == 2            # E=true with I=OFF, for both T values
    assert all(s["assignment"]["ENGINE_RUNNING == true"] and not s["assignment"]["IGNITION_SW == ON"] for s in skipped)

    tests, skipped = Generator(demo, strategy="mcdc").plan_requirement(req)
    assert len(tests) == 3
    assert skipped[0]["note"] == "independent effect of IGNITION_SW = ON cannot be shown"

    tests, skipped = Generator(demo, strategy="pairwise").plan_requirement(req)
    assert [s["note"] for s in skipped] == ["value pair never possible"]
    assert set(skipped[0]["assignment"]) == {"ENGINE_RUNNING == true", "IGNITION_SW == ON"}


def test_select_rows_pairwise_covers_every_possible_pair():
    impossible = lambda r: "nope" if (r[0] and not r[1]) else None
    rows, skipped = select_rows(4, "pairwise", 0, impossible)
    assert all(not (r[0] and not r[1]) for r in rows)
    for i in range(4):
        for j in range(i + 1, 4):
            for a in (True, False):
                for b in (True, False):
                    possible = not ((i, j, a, b) == (0, 1, True, False))
                    covered = any(r[i] == a and r[j] == b for r in rows)
                    assert covered == possible


def test_requirement_that_can_never_trigger(demo):
    demo.raw_requirements.append({"id": "SRS-099", "text": "When IGNITION_SW is OFF and ENGINE_RUNNING is true, "
                                                         "the software shall set FUEL_LOW within 10 ms"})
    demo.requirements.append(build_requirement(demo.raw_requirements[-1]))
    resolve_all(demo)
    assert any(i.code == "NEVER_TRIGGERED" and i.requirement_id == "SRS-099" for i in DependencyGraph(demo).issues())
    suite = Generator(demo).run(["SRS-099"])
    assert all(t.kind == "negative" for t in suite.tests)
    assert any(i.code == "NO_POSITIVE_TEST" for i in suite.issues)
