"""Project folder I/O and requirement linting.

A project is a plain folder:
    rdtg.yaml            ProjectConfig
    catalog.yaml        Catalog (connection points, messages, actions)
    requirements.yaml   {requirements: [{id, text, [timing_ack], [strategy], [tags]}]}
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from .model import Catalog, Direction, Issue, ProjectConfig, Requirement, SignalKind
from .parse import parse_requirement, resolve

_STRUCTURED_KEYS = ("trigger", "conditions", "response")
_USER_KEYS = ("id", "text", "timing_ack", "strategy", "tags")


@dataclass
class Project:
    root: Optional[Path]
    config: ProjectConfig
    catalog: Catalog
    requirements: list[Requirement]
    raw_requirements: list[dict[str, Any]] = field(default_factory=list)

    # ------------------------------------------------------------------ loading

    @classmethod
    def load(cls, root: str | Path) -> "Project":
        root = Path(root)
        config = ProjectConfig(**_read_yaml(root / "rdtg.yaml"))
        catalog = Catalog(**_read_yaml(root / "catalog.yaml"))
        raw = _read_yaml(root / "requirements.yaml").get("requirements", []) or []
        return cls.from_data(config, catalog, raw, root)

    @classmethod
    def from_data(cls, config: ProjectConfig, catalog: Catalog, raw: list[dict], root=None) -> "Project":
        reqs = [build_requirement(r) for r in raw]
        return cls(root=root, config=config, catalog=catalog, requirements=reqs, raw_requirements=raw)

    def save(self, root: str | Path | None = None) -> None:
        root = Path(root or self.root)
        root.mkdir(parents=True, exist_ok=True)
        _write_yaml(root / "rdtg.yaml", self.config.model_dump(mode="json", exclude_defaults=True))
        _write_yaml(root / "catalog.yaml", self.catalog.model_dump(mode="json", exclude_defaults=True))
        _write_yaml(root / "requirements.yaml", {"requirements": self.raw_requirements})
        self.root = root

    def requirement(self, req_id: str) -> Optional[Requirement]:
        return next((r for r in self.requirements if r.id == req_id), None)

    # ------------------------------------------------------------------ linting

    def lint(self) -> list[Issue]:
        issues: list[Issue] = []
        signals = self.catalog.signals()
        seen: set[str] = set()
        for req in self.requirements:
            def add(level, code, msg):
                issues.append(Issue(level=level, code=code, message=msg, requirement_id=req.id))

            if req.id in seen:
                add("error", "DUPLICATE_ID", f"duplicate requirement id {req.id}")
            seen.add(req.id)
            if not req.parsed:
                add("error", "UNPARSED", req.parse_error or "could not parse requirement")
                continue
            for err in req.resolve_errors:
                add("error", "UNRESOLVED", err)
            if not req.response:
                add("error", "NO_RESPONSE", "requirement has no observable response")
            for c in req.response:
                s = signals.get(c.signal)
                if s and s.direction == Direction.input:
                    add("warning", "RESPONSE_IS_INPUT",
                        f"response signal {c.signal} is a rig input — the SUT cannot drive it")
            for c in req.stimuli():
                s = signals.get(c.signal)
                if s and s.kind == SignalKind.analog and c.op in ("==", "!=") and s.resolution < 1:
                    add("info", "ANALOG_EQUALITY",
                        f"exact equality on analog signal {c.signal}; consider a range")
            by_sig: dict[str, list] = {}
            for c in req.stimuli():
                by_sig.setdefault(c.signal, []).append(c)
            for sig, cs in by_sig.items():
                eqs = {repr(c.value) for c in cs if c.op == "=="}
                if len(eqs) > 1:
                    add("error", "SELF_CONFLICT", f"{sig} must equal several values at once: {eqs}")
            has_timing = req.timing and (req.timing.max_ms or req.timing.min_ms)
            if not has_timing and not req.timing_ack and req.response:
                add("warning", "NO_TIMING",
                    f"no timing specified — tests will use the default timeout "
                    f"({self.config.default_timeout_ms:g} ms). Add 'within N ms' or set timing_ack.")
            if req.timing and req.timing.min_ms and req.timing.max_ms and req.timing.min_ms > req.timing.max_ms:
                add("error", "BAD_TIMING", "minimum time is greater than maximum time")
        return issues


def build_requirement(raw: dict[str, Any]) -> Requirement:
    if any(k in raw for k in _STRUCTURED_KEYS):
        req = Requirement(**raw)
        req.parsed = True
    else:
        req = parse_requirement(str(raw["id"]), str(raw.get("text", "")))
        for k in _USER_KEYS[2:]:
            if k in raw:
                setattr(req, k, raw[k])
    return req


def resolve_all(project: Project) -> None:
    """Resolve signal names and values against the catalog (in place)."""
    for req in project.requirements:
        if req.parsed:
            req.resolve_errors = resolve(req, project.catalog)


def open_project(root: str | Path) -> Project:
    p = Project.load(root)
    resolve_all(p)
    return p


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _write_yaml(path: Path, data) -> None:
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)
