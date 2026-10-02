"""Requirements compounding to make tests impossible, with no declared constraints."""
from rdtg.generate import Generator
from rdtg.model import Catalog, ConnectionPoint, Direction, ProjectConfig
from rdtg.project import Project, resolve_all


def project(*texts):
    cat = Catalog(connection_points=[
        ConnectionPoint(name=n, direction=Direction.input) for n in ("A", "B")
    ] + [ConnectionPoint(name=n, direction=Direction.output) for n in ("C", "OUT")])
    p = Project.from_data(ProjectConfig(), cat, [{"id": f"R{i + 1}", "text": t} for i, t in enumerate(texts)])
    resolve_all(p)
    assert all(r.parsed and not r.resolve_errors for r in p.requirements)
    return p


def test_negative_case_where_other_requirements_produce_the_response_anyway():
    """R1's negative case "B false" switches on C (R3), and A with C sets OUT (R2). So with A true and
    B false, OUT *will* be set; a test expecting no response from R1 would fail on correct software."""
    p = project(
        "When A is true and B is true, the software shall set OUT within 10 ms",
        "When A is true and C is true, the software shall set OUT within 10 ms",
        "When B is false, the software shall set C within 10 ms",
    )
    tests, skipped = Generator(p, strategy="all").plan_requirement(p.requirement("R1"))
    forced = [s for s in skipped if s["assignment"] == {"B == true": False, "A == true": True}]
    assert len(forced) == 1
    assert "other requirements produce the response" in forced[0]["note"]
    assert "R2" in forced[0]["reason"] and "R3" in forced[0]["reason"]
    # the remaining negative cases are still generated
    assert {tuple(t.assignment.values()) for t in tests} == {(True, True), (False, False), (True, False)}


def test_positive_case_impossible_when_response_is_already_present():
    """R2 sets OUT whenever B is true, so R1's trigger (A) can never be shown to cause OUT."""
    p = project(
        "When A is true and B is true, the software shall set OUT within 10 ms",
        "When B is true, the software shall set OUT within 10 ms",
    )
    suite = Generator(p).run(["R1"])
    assert all(t.kind == "negative" for t in suite.tests)
    positive = [u for u in suite.untestable if u["note"].startswith("positive case")]
    assert positive and "already present before the trigger" in positive[0]["reason"]
    assert any(i.code == "NO_POSITIVE_TEST" for i in suite.issues)
