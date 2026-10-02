"""Emit test documentation (Markdown/HTML) and executable pytest tests from a Suite."""
from __future__ import annotations

import datetime as _dt
import os
import re
from pathlib import Path
from typing import Optional

from jinja2 import ChoiceLoader, Environment, FileSystemLoader

from .generate import Suite
from .model import Step, TestCase, fmt_value
from .project import Project

_TEMPLATES = Path(__file__).parent / "templates"
_SYM = {"==": "=", "!=": "≠", "<": "<", "<=": "≤", ">": ">", ">=": "≥"}


def _cond(signal, op, value) -> str:
    return f"{signal} {_SYM[op]} {fmt_value(value)}"


def step_text(s: Step) -> tuple[str, str]:
    """(action, expected result) wording for test procedure documents."""
    if s.kind == "set":
        return f"Set {s.signal} to {fmt_value(s.value)}", "—"
    if s.kind == "send":
        flds = ", ".join(f"{k} = {fmt_value(v)}" for k, v in s.fields.items())
        return f"Send message {s.message} with {flds}", "—"
    if s.kind == "action":
        return f"Perform procedure '{s.action}'", "Procedure completes"
    if s.kind == "wait":
        return f"Wait {s.ms:g} ms", "—"
    if s.kind == "wait_until":
        via = f" ({s.note})" if s.note else ""
        return f"Wait for {_cond(s.signal, s.op, s.value)}{via}", f"{_cond(s.signal, s.op, s.value)} within {s.ms:g} ms"
    if s.kind == "check":
        return f"Verify {_cond(s.signal, s.op, s.value)}", _cond(s.signal, s.op, s.value)
    if s.kind == "expect":
        return f"Monitor {s.signal}", f"{_cond(s.signal, s.op, s.value)} within {s.ms:g} ms of stimulus"
    if s.kind == "expect_not":
        if s.note.startswith("must not respond earlier"):
            return f"Monitor {s.signal}", f"{_cond(s.signal, s.op, s.value)} does NOT occur before {s.ms:g} ms"
        return f"Monitor {s.signal}", f"{_cond(s.signal, s.op, s.value)} does NOT occur within {s.ms:g} ms"
    return s.kind, ""


def fmt_ms(ms: float) -> str:
    if ms >= 60000:
        return f"{ms / 60000:.1f} min"
    if ms >= 1000:
        return f"{ms / 1000:.2f} s"
    return f"{ms:g} ms"


def _py_ident(s: str) -> str:
    return re.sub(r"\W", "_", s)


def _env(project: Project) -> Environment:
    loaders = []
    if project.root and (Path(project.root) / "templates").is_dir():
        loaders.append(FileSystemLoader(str(Path(project.root) / "templates")))
    loaders.append(FileSystemLoader(str(_TEMPLATES)))
    env = Environment(loader=ChoiceLoader(loaders), trim_blocks=True, lstrip_blocks=True,
                      keep_trailing_newline=True)
    env.filters.update(step_text=step_text, fmt_ms=fmt_ms, py=repr, ident=_py_ident, val=fmt_value)
    return env


def _context(project: Project, suite: Suite, strategy: Optional[str]) -> dict:
    reqs = {r.id: r for r in project.requirements}
    trace: dict[str, list[TestCase]] = {}
    for t in suite.tests:
        trace.setdefault(t.requirement_id, []).append(t)
    setup_use: dict[str, list[str]] = {}
    for t in suite.tests:
        for rid in t.traces[1:]:
            setup_use.setdefault(rid, []).append(t.id)
    return dict(project=project, config=project.config, suite=suite, reqs=reqs, trace=trace,
                setup_use=setup_use, strategy=strategy or project.config.strategy,
                generated=_dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
                n_pos=sum(t.kind == "positive" for t in suite.tests),
                n_neg=sum(t.kind == "negative" for t in suite.tests))


def render_docs(project: Project, suite: Suite, strategy: Optional[str] = None) -> dict[str, str]:
    env, ctx = _env(project), _context(project, suite, strategy)
    return {"test_procedures.md": env.get_template("procedures.md.j2").render(**ctx),
            "test_procedures.html": env.get_template("procedures.html.j2").render(**ctx)}


def render_pytest(project: Project, suite: Suite, out_dir: Path,
                  strategy: Optional[str] = None) -> dict[str, str]:
    env, ctx = _env(project), _context(project, suite, strategy)
    files: dict[str, str] = {}
    root = Path(project.root).resolve() if project.root else None
    rel = os.path.relpath(root, out_dir.resolve()).replace("\\", "/") if root else "."
    files["conftest.py"] = env.get_template("conftest.py.j2").render(project_rel=rel, **ctx)
    for rid, tests in ctx["trace"].items():
        files[f"test_{_py_ident(rid)}.py"] = env.get_template("test_module.py.j2").render(
            req=ctx["reqs"][rid], tests=tests, **ctx)
    return files


def write_outputs(project: Project, suite: Suite, out_dir: str | Path,
                  strategy: Optional[str] = None) -> list[Path]:
    out = Path(out_dir)
    docs, tests = out / "docs", out / "tests"
    docs.mkdir(parents=True, exist_ok=True)
    tests.mkdir(parents=True, exist_ok=True)
    for old in tests.glob("test_*.py"):
        old.unlink()
    written = []
    for name, text in render_docs(project, suite, strategy).items():
        (docs / name).write_text(text, encoding="utf-8")
        written.append(docs / name)
    for name, text in render_pytest(project, suite, tests, strategy).items():
        (tests / name).write_text(text, encoding="utf-8")
        written.append(tests / name)
    return written
