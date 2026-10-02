"""Command line interface.

    rdtg lint     PROJECT
    rdtg graph    PROJECT [--dot FILE]
    rdtg generate WORKSPACE [-o OUT] [--strategy S] [--max-cases N] [--req ID ...]
    rdtg run      OUT|WORKSPACE [--target NAME] [--param KEY=VALUE ...] [pytest args]
    rdtg targets  WORKSPACE [--check [NAME]]
    rdtg mutate   PROJECT [--strategy S]
    rdtg serve    [WORKSPACE] [--port 8000]
"""
from __future__ import annotations

import argparse
import math
import os
import subprocess
import sys
from pathlib import Path

from .emit import fmt_ms, write_outputs
from .generate import STRATEGIES, Generator
from .graph import DependencyGraph, negate
from .harness import FAULT_KINDS, mutation_matrix, open_target, release_target
from .project import open_project


def _print_issues(issues) -> int:
    errors = 0
    for i in issues:
        errors += i.level == "error"
        print(f"  {i.level.upper():7} {i.requirement_id or '':10} {i.code:18} {i.message}")
    return errors


def cmd_lint(a) -> int:
    p = open_project(a.project)
    g = DependencyGraph(p)
    issues = p.lint() + g.issues()
    print(f"{len(p.requirements)} requirements, {len(p.catalog.signals())} signals")
    errors = _print_issues(issues)
    print("OK" if not issues else f"{errors} error(s), {len(issues) - errors} warning(s)/info")
    return 1 if errors else 0


def cmd_graph(a) -> int:
    p = open_project(a.project)
    g = DependencyGraph(p)
    if a.dot:
        Path(a.dot).write_text(g.to_dot(), encoding="utf-8")
        print(f"wrote {a.dot}")
    print("Cheapest way to establish each requirement's stimuli:\n")
    for r in g.requirements:
        print(f"{r.id}: {r.text}")
        for c in r.stimuli():
            for variant in (c, negate(c)):
                cost = g.cost(variant)
                best = g.providers_for(variant)[:1]
                via = "already true after reset" if cost == 0 else (best[0][0].label if best else "-")
                cs = "UNREACHABLE" if math.isinf(cost) else fmt_ms(cost)
                print(f"    {str(variant):40} {cs:>12}  {via if cost == 0 else 'via ' + via}")
    return 0


def cmd_generate(a) -> int:
    p = open_project(a.project)
    lint_errors = [i for i in p.lint() if i.level == "error"]
    if lint_errors:
        print("lint errors (affected requirements will be skipped):")
        _print_issues(lint_errors)
    suite = Generator(p, strategy=a.strategy, max_cases=a.max_cases).run(a.req or None)
    files = write_outputs(p, suite, a.out or Path(a.project) / "out", a.strategy)
    print(f"{len(suite.tests)} test cases, estimated {fmt_ms(suite.est_ms)}")
    for t in suite.tests:
        warn = f"  ⚠ {len(t.warnings)} warning(s)" if t.warnings else ""
        print(f"  {t.id:18} {t.kind:8} {fmt_ms(t.est_ms):>9}  {t.title}{warn}")
    if suite.untestable:
        print(f"\n{len(suite.untestable)} combination(s) not testable:")
        for u in suite.untestable:
            combo = ", ".join(f"{k} {'✔' if v else '✘'}" for k, v in u["assignment"].items())
            note = f" [{u['note']}]" if u["note"] else ""
            print(f"  {u['requirement_id']:10} {combo}{note}\n             because {u['reason']}")
    for i in suite.issues:
        print(f"  {i.level.upper()} {i.requirement_id or ''} {i.code}: {i.message}")
    for f in files:
        print(f"wrote {f}")
    return 0


def cmd_run(a) -> int:
    env = dict(os.environ)
    if a.target:
        env["RDTG_TARGET"] = a.target
    if a.param:
        env["RDTG_TARGET_PARAMS"] = ";".join(a.param)
    out = Path(a.out)
    if (out / "rdtg.yaml").is_file():  # given a workspace: use its default output folder
        out = out / "out"
    tests = out / "tests"
    return subprocess.call([sys.executable, "-m", "pytest", str(tests), "-q", *a.pytest_args], env=env)


def cmd_targets(a) -> int:
    p = open_project(a.project)
    if not p.config.targets:
        print("no targets configured (add `targets:` to rdtg.yaml)")
        return 1
    default = p.config.target().name
    for t in p.config.targets:
        mark = "*" if t.name == default else " "
        print(f"{mark} {t.name:32} [{t.kind}]  {t.description}")
        for transport, spec in t.adapters.items():
            print(f"      {transport:14} {spec.get('type')}")
    if a.check:
        names = [t.name for t in p.config.targets] if a.check == "all" else [a.check]
        print()
        ok = True
        for n in names:
            try:
                open_target(p, n)
                print(f"  {n}: available")
            except Exception as e:
                ok = False
                print(f"  {n}: UNAVAILABLE — {e}")
            finally:
                release_target()
        return 0 if ok else 1
    return 0


def cmd_mutate(a) -> int:
    """Inject faults into the requirements model and check the generated suite detects them."""
    p = open_project(a.project)
    suite = Generator(p, strategy=a.strategy).run()
    reqs = [r for r in p.requirements if r.parsed and not r.resolve_errors and r.response]
    print(f"{len(suite.tests)} test cases, strategy {a.strategy or p.config.strategy}\n")
    result = mutation_matrix(p, suite)
    w = max(len(k) for k in FAULT_KINDS) + 1
    print(f"{'requirement':12} " + " ".join(f"{k:>{w}}" for k in FAULT_KINDS))
    for rid, row in result["rows"].items():
        print(f"{rid:12} " + " ".join(f"{row[k]:>{w}}" for k in FAULT_KINDS))
    c = result["counts"]
    total = sum(c.values()) - c['equivalent']
    print(f"\ncaught (test failed): {c['caught']}/{total}   "
          f"flagged (inconclusive setup): {c['flagged']}   missed: {c['MISSED']}   "
          f"equivalent (unobservable): {c['equivalent']}")
    return 0 if c["MISSED"] == 0 else 1


def cmd_serve(a) -> int:
    import uvicorn
    from .server import create_app
    uvicorn.run(create_app(a.project), host=a.host, port=a.port)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="rdtg", description="Requirements-driven test generation")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("lint", help="check requirements against the catalog")
    s.add_argument("project"); s.set_defaults(fn=cmd_lint)

    s = sub.add_parser("graph", help="show dependency graph / setup costs")
    s.add_argument("project"); s.add_argument("--dot"); s.set_defaults(fn=cmd_graph)

    s = sub.add_parser("generate", help="generate test documentation + pytest tests")
    s.add_argument("project"); s.add_argument("-o", "--out", help="output folder (default: WORKSPACE/out)")
    s.add_argument("--strategy", help=f"one of {STRATEGIES}")
    s.add_argument("--max-cases", type=int); s.add_argument("--req", nargs="*")
    s.set_defaults(fn=cmd_generate)

    s = sub.add_parser("run", help="run generated tests with pytest")
    s.add_argument("out", help="output folder, or a workspace (uses WORKSPACE/out)")
    s.add_argument("--target", help="target name from rdtg.yaml (default: default_target)")
    s.add_argument("--param", action="append", default=[], metavar="KEY=VALUE",
                   help="target parameter, e.g. --param bugs=slow_spinup (repeatable)")
    s.set_defaults(fn=cmd_run)

    s = sub.add_parser("targets", help="list the workspace's test targets")
    s.add_argument("project"); s.add_argument("--check", nargs="?", const="all", metavar="NAME",
                                             help="try to open the target(s) and report availability")
    s.set_defaults(fn=cmd_targets)

    s = sub.add_parser("mutate", help="check the suite detects injected SUT faults")
    s.add_argument("project"); s.add_argument("--strategy"); s.set_defaults(fn=cmd_mutate)

    s = sub.add_parser("serve", help="start the web UI")
    s.add_argument("project", nargs="?", default=None, help="workspace to open (default: show the workspace picker)"); s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000); s.set_defaults(fn=cmd_serve)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    a, extra = ap.parse_known_args(argv)
    if extra and a.cmd != "run":
        ap.error(f"unrecognized arguments: {' '.join(extra)}")
    a.pytest_args = extra  # anything unknown to `rdtg run` is passed through to pytest
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
