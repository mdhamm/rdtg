import pytest

from rdtg.model import Pattern
from rdtg.parse import parse_requirement


def p(text):
    r = parse_requirement("R", text)
    assert r.parsed, r.parse_error
    return r


def test_event_driven_with_timing():
    r = p("When IGNITION_SW is ON and FUEL_PUMP_OK is true, the software shall set ENGINE_RUNNING to true within 500 ms.")
    assert r.pattern == Pattern.event
    assert (r.trigger.signal, r.trigger.value) == ("IGNITION_SW", "ON")
    assert [(c.signal, c.value) for c in r.conditions] == [("FUEL_PUMP_OK", True)]
    assert [(c.signal, c.value) for c in r.response] == [("ENGINE_RUNNING", True)]
    assert r.timing.max_ms == 500


def test_trailing_when_clause_and_seconds():
    r = p("The software shall clear FAULT_LAMP when RESET_BTN is pressed within 2 s")
    assert r.pattern == Pattern.event
    assert r.trigger.signal == "RESET_BTN" and r.trigger.value == "pressed"
    assert r.response[0].signal == "FAULT_LAMP" and r.response[0].value is False
    assert r.timing.max_ms == 2000


def test_state_complex_unwanted_optional():
    assert p("While MAINT is true, the software shall set INHIBIT").pattern == Pattern.state
    c = p("While MAINT is true, when IGN is ON, the software shall set INHIBIT within 10 ms")
    assert c.pattern == Pattern.complex and c.trigger.signal == "IGN" and c.conditions[0].signal == "MAINT"
    u = p("If FUEL_PRESSURE is less than 20, then the software shall set FUEL_LOW to true")
    assert u.pattern == Pattern.unwanted and u.trigger.op == "<" and u.trigger.value == 20
    o = p("Where APU_INSTALLED is true, the software shall report APU.status as OK")
    assert o.pattern == Pattern.optional and o.response[0].signal == "APU.status"


@pytest.mark.parametrize("phrase,op,val", [
    ("X is greater than 5", ">", 5), ("X is at least 5", ">=", 5), ("X is below 2.5", "<", 2.5),
    ("X is not ON", "!=", "ON"), ("X >= 3", ">=", 3), ("X is set", "==", True),
    ("X is cleared", "==", False),
])
def test_condition_operators(phrase, op, val):
    r = p(f"When {phrase}, the software shall set Y")
    assert (r.trigger.op, r.trigger.value) == (op, val)


def test_timing_ranges():
    r = p("When A is true, the software shall set B between 20 and 80 ms")
    assert (r.timing.min_ms, r.timing.max_ms) == (20, 80)
    r = p("When A is true, the software shall set B no earlier than 1 s")
    assert r.timing.min_ms == 1000 and r.timing.max_ms is None


def test_multiple_responses():
    r = p("When A is true, the software shall set B to 3 and C to OFF and clear D")
    assert [(c.signal, c.value) for c in r.response] == [("B", 3), ("C", "OFF"), ("D", False)]


def test_ubiquitous_and_unparsed():
    assert p("The software shall set HEARTBEAT").pattern == Pattern.ubiquitous
    bad = parse_requirement("R", "Engine goes vroom")
    assert not bad.parsed and "shall" in bad.parse_error


def test_resolution_against_catalog(demo):
    r = demo.requirement("SRS-001")
    assert r.resolve_errors == []
    assert r.trigger.value == "ON"           # enum value
    assert r.conditions[0].value is True     # discrete
    lint = {(i.requirement_id, i.code) for i in demo.lint()}
    assert ("SRS-005", "NO_TIMING") in lint
