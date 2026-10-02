import math

from rdtg.graph import DependencyGraph, negate
from rdtg.model import Catalog, ConnectionPoint, Condition, Direction, ProjectConfig
from rdtg.project import Project, resolve_all


def test_cheapest_path_uses_requirement_not_slow_action(demo):
    g = DependencyGraph(demo)
    goal = Condition(signal="ENGINE_RUNNING", value=True)
    best, cost = g.providers_for(goal)[0]
    assert best.id == "req:SRS-001"
    assert cost == 70 + 70 + 500
    plan = g.plan([goal])
    assert plan.ok
    assert [s.kind for s in plan.steps] == ["set", "set", "wait_until"]


def test_disallowed_provider_falls_back_to_action(demo):
    demo.config.disallowed_providers = ["req:SRS-001"]
    g = DependencyGraph(demo)
    best, cost = g.providers_for(Condition(signal="ENGINE_RUNNING", value=True))[0]
    assert best.id == "action:start_engine_full" and cost == 4000


def test_unreachable_output_is_reported():
    cat = Catalog(connection_points=[
        ConnectionPoint(name="IN", direction=Direction.input),
        ConnectionPoint(name="ORPHAN", direction=Direction.output),
        ConnectionPoint(name="OUT", direction=Direction.output),
    ])
    p = Project.from_data(ProjectConfig(), cat, [
        {"id": "R1", "text": "When IN is true and ORPHAN is true, the software shall set OUT within 10 ms"}])
    resolve_all(p)
    g = DependencyGraph(p)
    assert math.isinf(g.cost(Condition(signal="ORPHAN", value=True)))
    assert not g.plan([Condition(signal="ORPHAN", value=True)]).ok
    assert any(i.code == "UNREACHABLE" and i.level == "error" for i in g.issues())


def test_negation_and_boundary_witness(demo):
    g = DependencyGraph(demo)
    c = Condition(signal="FUEL_PRESSURE", op="<", value=20)
    assert negate(c).op == ">="
    assert g.plan([c]).steps[0].value == 19
    assert g.plan([Condition(signal="THROTTLE", op=">", value=20)]).steps[0].value == 21


def test_trigger_edge_is_reestablished(demo):
    """Using SRS-001 as a setup path when IGNITION is already ON requires toggling it."""
    g = DependencyGraph(demo)
    state = g.initial_state()
    state.update({"IGNITION_SW": "ON", "FUEL_PUMP_OK": False})
    plan = g.plan([Condition(signal="ENGINE_RUNNING", value=True)], state)
    assert plan.ok
    ign = [s.value for s in plan.steps if s.signal == "IGNITION_SW"]
    assert ign == ["OFF", "ON"]


def test_graph_export(demo):
    g = DependencyGraph(demo)
    ids = {n["id"] for n in g.to_dict()["nodes"]}
    assert "p:req:SRS-001" in ids and "f:ENGINE_RUNNING==true" in ids
    assert "digraph" in g.to_dot()
