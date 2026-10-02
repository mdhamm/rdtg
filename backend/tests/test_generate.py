import itertools
import subprocess
import sys

import pytest

from rdtg.emit import render_docs, render_pytest, write_outputs
from rdtg.generate import Generator, assignments
from rdtg.harness import RequirementsModel, Rig, execute, mutation_matrix


@pytest.mark.parametrize("n", range(0, 6))
def test_strategy_sizes(n):
    assert len(assignments(n, "all")) == 2 ** n
    assert len(assignments(n, "mcdc")) == n + 1
    assert assignments(n, "positive_only") == [tuple([True] * n)]
    for s in ("all", "mcdc", "pairwise", "random:3"):
        assert assignments(n, s)[0] == tuple([True] * n)


@pytest.mark.parametrize("n", range(2, 8))
def test_pairwise_covers_all_pairs(n):
    rows = assignments(n, "pairwise")
    for i, j in itertools.combinations(range(n), 2):
        assert {(r[i], r[j]) for r in rows} == {(a, b) for a in (True, False) for b in (True, False)}
    if n >= 4:
        assert len(rows) < 2 ** n


def test_random_is_reproducible():
    assert assignments(6, "random:5", seed=1) == assignments(6, "random:5", seed=1)
    assert len(assignments(6, "random:5")) == 5


def test_generated_suite_is_consistent_with_requirements_model(demo):
    suite = Generator(demo).run()
    assert len(suite.tests) == 23
    rig = Rig(RequirementsModel(demo), demo.catalog)
    results = [execute(t, rig) for t in suite.tests]
    assert all(r["status"] == "passed" for r in results), [r for r in results if r["status"] != "passed"]


def test_setup_clears_response_using_other_requirement(demo):
    """SRS-005 (engine off when ignition OFF) is only meaningful if the engine runs first."""
    tc = Generator(demo).for_requirement(demo.requirement("SRS-005"))[0]
    assert "SRS-001" in tc.traces
    assert not tc.warnings


def test_max_cases_and_override(demo):
    suite = Generator(demo, strategy="all", max_cases=2).run()
    assert all(len(suite.for_requirement(r.id)) <= 2 for r in demo.requirements)


def test_mcdc_detects_all_injected_faults(demo):
    res = mutation_matrix(demo, Generator(demo).run())
    assert res["counts"]["MISSED"] == 0
    weak = mutation_matrix(demo, Generator(demo, strategy="positive_only").run())
    assert weak["counts"]["MISSED"] > 0


def test_emitted_pytest_compiles_and_docs_trace(demo, tmp_path):
    suite = Generator(demo).run()
    for name, src in render_pytest(demo, suite, tmp_path).items():
        compile(src, name, "exec")
    docs = render_docs(demo, suite)
    for r in demo.requirements:
        assert r.id in docs["test_procedures.md"]
    assert "NOT COVERED" not in docs["test_procedures.html"]


def test_emitted_tests_pass_against_simulated_hardware_target(demo, tmp_path):
    """End to end: generated pytest files vs. the independent ECU-7 program (default target)."""
    write_outputs(demo, Generator(demo).run(), tmp_path)
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", str(tmp_path / "tests"),
                        "-p", "no:cacheprovider"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "23 passed" in r.stdout
