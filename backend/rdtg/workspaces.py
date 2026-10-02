"""Workspaces: project folders on disk, plus the user's recent-workspace list.

A folder is a workspace if it contains rdtg.yaml. The recent list lives in ~/.rdtg/recent.json
(override with the RDTG_HOME environment variable).
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import string
from pathlib import Path
from typing import Optional

import yaml

CONFIG_FILE = "rdtg.yaml"
MAX_RECENT = 12


def home_dir() -> Path:
    return Path(os.environ.get("RDTG_HOME") or Path.home() / ".rdtg")


def is_workspace(path: str | Path) -> bool:
    return (Path(path) / CONFIG_FILE).is_file()


def workspace_name(path: str | Path) -> str:
    try:
        with open(Path(path) / CONFIG_FILE, encoding="utf-8") as f:
            return (yaml.safe_load(f) or {}).get("name") or Path(path).name
    except OSError:
        return Path(path).name


# --------------------------------------------------------------------------- recent list


def _recent_file() -> Path:
    return home_dir() / "recent.json"


def load_recent() -> list[dict]:
    try:
        items = json.loads(_recent_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for it in items:
        p = it.get("path")
        if p:
            out.append({"path": p, "name": workspace_name(p) if is_workspace(p) else it.get("name", Path(p).name),
                        "opened": it.get("opened"), "exists": is_workspace(p)})
    return out


def _save_recent(items: list[dict]) -> None:
    f = _recent_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps([{k: it[k] for k in ("path", "name", "opened")} for it in items], indent=2),
                 encoding="utf-8")


def add_recent(path: str | Path) -> None:
    p = str(Path(path).resolve())
    items = [it for it in load_recent() if os.path.normcase(it["path"]) != os.path.normcase(p)]
    items.insert(0, {"path": p, "name": workspace_name(p),
                     "opened": _dt.datetime.now().isoformat(timespec="seconds")})
    _save_recent(items[:MAX_RECENT])


def remove_recent(path: str) -> None:
    _save_recent([it for it in load_recent() if os.path.normcase(it["path"]) != os.path.normcase(path)])


# --------------------------------------------------------------------------- create


def create_workspace(path: str | Path, name: str) -> Path:
    """Create a new, empty workspace. The folder must not exist or must be empty."""
    root = Path(path).expanduser().resolve()
    if root.exists():
        if not root.is_dir():
            raise ValueError(f"{root} is a file")
        if is_workspace(root):
            raise ValueError(f"{root} is already a workspace — open it instead")
        if any(root.iterdir()):
            raise ValueError(f"{root} is not empty")
    root.mkdir(parents=True, exist_ok=True)
    (root / CONFIG_FILE).write_text(yaml.safe_dump({"name": name or root.name, "strategy": "mcdc",
                                                    "default_timeout_ms": 1000, "targets": []},
                                                   sort_keys=False), encoding="utf-8")
    (root / "catalog.yaml").write_text(
        "# Interface catalog: how the test rig stimulates and observes the software under test.\n"
        "connection_points: []\nmessages: []\nactions: []\n", encoding="utf-8")
    (root / "requirements.yaml").write_text("requirements: []\n", encoding="utf-8")
    return root


# --------------------------------------------------------------------------- folder browsing


def list_dir(path: Optional[str]) -> dict:
    """Subfolders of `path` (default: home), flagging which ones are workspaces."""
    if not path:
        base = Path.home()
    else:
        base = Path(path).expanduser()
    base = base.resolve()
    if not base.is_dir():
        raise ValueError(f"{base} is not a folder")
    dirs = []
    try:
        for child in sorted(base.iterdir(), key=lambda c: c.name.lower()):
            if child.name.startswith(".") or child.name.startswith("$"):
                continue
            try:
                if child.is_dir():
                    dirs.append({"name": child.name, "path": str(child), "is_workspace": is_workspace(child)})
            except OSError:
                continue
    except PermissionError:
        pass
    parent = str(base.parent) if base.parent != base else None
    roots = [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")] if os.name == "nt" else ["/"]
    return {"path": str(base), "parent": parent, "is_workspace": is_workspace(base), "dirs": dirs,
            "roots": roots, "home": str(Path.home())}
