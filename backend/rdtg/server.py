"""FastAPI server for the web UI. `rdtg serve [WORKSPACE]` starts it."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .emit import render_docs, render_pytest, write_outputs
from .generate import STRATEGIES, Generator
from .graph import DependencyGraph, negate
from .harness import FAULT_KINDS, Rig, execute, mutation_matrix, open_target, release_target
from .model import Catalog, ProjectConfig
from .parse import parse_requirement, resolve
from .project import Project, open_project
from . import workspaces as ws

STATIC = Path(__file__).parent / "static"


class GenRequest(BaseModel):
    strategy: Optional[str] = None
    max_cases: Optional[int] = None
    requirement_ids: Optional[list[str]] = None


class RunRequest(GenRequest):
    target: Optional[str] = None          # name from rdtg.yaml (default: default_target)
    params: dict[str, Any] = {}           # overrides of the target's params


class TargetRequest(BaseModel):
    target: Optional[str] = None
    params: dict[str, Any] = {}


class ExportRequest(GenRequest):
    out_dir: str = "out"


class ParseRequest(BaseModel):
    id: str = "PREVIEW"
    text: str


class OpenRequest(BaseModel):
    path: str


class CreateRequest(BaseModel):
    path: str
    name: str = ""


class PlanRequest(BaseModel):
    signal: str
    op: str = "=="
    value: Any = True


def create_app(project_dir: Optional[str | Path] = None) -> FastAPI:
    """With project_dir, that workspace starts open; otherwise the UI shows the workspace picker."""
    app = FastAPI(title="RDTG")
    state: dict[str, Optional[Project]] = {"p": None}

    def open_ws(path: str | Path) -> Project:
        root = Path(path).expanduser().resolve()
        if not ws.is_workspace(root):
            raise HTTPException(400, f"{root} is not an RDTG workspace (no {ws.CONFIG_FILE})")
        try:
            state["p"] = open_project(root)
        except Exception as e:  # malformed YAML etc.
            raise HTTPException(400, f"cannot open {root}: {e}")
        ws.add_recent(root)
        return state["p"]

    if project_dir:
        root = Path(project_dir).resolve()
        if not ws.is_workspace(root):
            raise SystemExit(f"{root} is not an RDTG workspace (no {ws.CONFIG_FILE})")
        state["p"] = open_project(root)
        ws.add_recent(root)

    def proj() -> Project:
        if state["p"] is None:
            raise HTTPException(409, "no workspace is open")
        return state["p"]

    def root() -> Path:
        return Path(proj().root)

    def reload() -> Project:
        state["p"] = open_project(root())
        return state["p"]

    # ------------------------------------------------------------------ workspaces

    @app.get("/api/workspace")
    def current_workspace():
        p = state["p"]
        return {"open": p is not None, "path": str(p.root) if p else None,
                "name": p.config.name if p else None}

    @app.get("/api/workspaces/recent")
    def recent():
        return ws.load_recent()

    @app.delete("/api/workspaces/recent")
    def forget(path: str):
        ws.remove_recent(path)
        return ws.load_recent()

    @app.post("/api/workspaces/open")
    def open_workspace(r: OpenRequest):
        open_ws(r.path)
        return current_workspace()

    @app.post("/api/workspaces/create")
    def create_workspace(r: CreateRequest):
        try:
            path = ws.create_workspace(r.path, r.name)
        except (ValueError, OSError) as e:
            raise HTTPException(400, str(e))
        open_ws(path)
        return current_workspace()

    @app.post("/api/workspaces/close")
    def close_workspace():
        state["p"] = None
        return current_workspace()

    @app.get("/api/fs/list")
    def fs_list(path: Optional[str] = None):
        try:
            return ws.list_dir(path)
        except (ValueError, OSError) as e:
            raise HTTPException(400, str(e))

    # ------------------------------------------------------------------ project

    def lint(p: Project):
        g = DependencyGraph(p)
        return [i.model_dump() for i in p.lint() + g.issues()]

    def suite_for(r: GenRequest):
        p = proj()
        return p, Generator(p, strategy=r.strategy, max_cases=r.max_cases).run(r.requirement_ids)

    @app.get("/api/project")
    def get_project():
        p = proj()
        return {
            "root": str(root()),
            "config": p.config.model_dump(mode="json"),
            "catalog": p.catalog.model_dump(mode="json"),
            "signals": {k: v.model_dump(mode="json") for k, v in p.catalog.signals().items()},
            "raw_requirements": p.raw_requirements,
            "requirements": [r.model_dump(mode="json") for r in p.requirements],
            "issues": lint(p),
            "strategies": list(STRATEGIES),
            "fault_kinds": FAULT_KINDS,
        }

    @app.put("/api/requirements")
    def put_requirements(raw: list[dict[str, Any]]):
        p = proj()
        ids = [str(r.get("id", "")).strip() for r in raw]
        if any(not i for i in ids):
            raise HTTPException(400, "every requirement needs an id")
        p.raw_requirements = [{k: v for k, v in r.items() if v not in (None, "", [])} for r in raw]
        p.save(root())
        reload()
        return get_project()

    @app.put("/api/catalog")
    def put_catalog(catalog: Catalog):
        p = proj()
        p.catalog = catalog
        p.save(root())
        reload()
        return get_project()

    @app.put("/api/config")
    def put_config(config: ProjectConfig):
        p = proj()
        p.config = config
        p.save(root())
        reload()
        return get_project()

    @app.post("/api/parse")
    def parse_preview(req: ParseRequest):
        r = parse_requirement(req.id, req.text)
        if r.parsed:
            r.resolve_errors = resolve(r, proj().catalog)
        tmp = Project.from_data(proj().config, proj().catalog, [])
        tmp.requirements = [r]
        return {"requirement": r.model_dump(mode="json"),
                "issues": [i.model_dump() for i in tmp.lint()]}

    @app.get("/api/graph")
    def get_graph():
        p = proj()
        g = DependencyGraph(p)
        d = g.to_dict()
        costs = {}
        for r in g.requirements:
            rows = []
            for c in r.stimuli():
                for v in (c, negate(c)):
                    cost = g.cost(v)
                    best = g.providers_for(v)[:1]
                    rows.append({"condition": str(v),
                                 "cost": None if cost == float("inf") else cost,
                                 "via": "initial state" if cost == 0 else (best[0][0].label if best else None)})
            costs[r.id] = rows
        d["setup_costs"] = costs
        return d

    @app.post("/api/plan")
    def plan(req: PlanRequest):
        from .model import Condition
        g = DependencyGraph(proj())
        res = g.plan([Condition(signal=req.signal, op=req.op, value=req.value)])
        return {"ok": res.ok, "cost": res.cost, "warnings": res.warnings,
                "steps": [s.model_dump(mode="json") for s in res.steps], "used": res.used}

    @app.post("/api/generate")
    def generate(r: GenRequest):
        p, suite = suite_for(r)
        tests_py = render_pytest(p, suite, root() / "out" / "tests", r.strategy)
        return {"tests": [t.model_dump(mode="json") for t in suite.tests],
                "issues": [i.model_dump() for i in suite.issues],
                "est_ms": suite.est_ms, "pytest": tests_py, "untestable": suite.untestable}

    @app.post("/api/docs", response_class=HTMLResponse)
    def docs(r: GenRequest):
        p, suite = suite_for(r)
        return render_docs(p, suite, r.strategy)["test_procedures.html"]

    @app.get("/api/docs.html", response_class=HTMLResponse)
    def docs_get(strategy: Optional[str] = None, max_cases: Optional[int] = None):
        return docs(GenRequest(strategy=strategy, max_cases=max_cases))

    @app.get("/api/docs.md")
    def docs_md(strategy: Optional[str] = None, max_cases: Optional[int] = None):
        p, suite = suite_for(GenRequest(strategy=strategy, max_cases=max_cases))
        return Response(render_docs(p, suite, strategy)["test_procedures.md"], media_type="text/markdown")

    @app.post("/api/export")
    def export(r: ExportRequest):
        p, suite = suite_for(r)
        out = Path(r.out_dir)
        if not out.is_absolute():
            out = root() / out
        files = write_outputs(p, suite, out, r.strategy)
        return {"files": [str(f) for f in files]}

    @app.post("/api/run")
    def run(r: RunRequest):
        p, suite = suite_for(r)
        try:
            name = p.config.target(r.target).name
        except KeyError as e:
            raise HTTPException(400, str(e.args[0]))
        try:
            rig = Rig(open_target(p, name, r.params), p.catalog)
            results = [execute(t, rig) for t in suite.tests]
        except Exception as e:  # target unavailable: driver missing, device unreachable, ...
            raise HTTPException(400, f"target '{name}' unavailable: {e}")
        finally:
            release_target()  # free the bench for other runs (e.g. the CLI)
        return {"results": results, "target": name}

    @app.post("/api/targets/check")
    def check_target(r: TargetRequest):
        p = proj()
        try:
            name = p.config.target(r.target).name
            open_target(p, name, r.params)
            return {"target": name, "available": True, "message": "available"}
        except Exception as e:
            return {"target": r.target, "available": False, "message": str(e)}
        finally:
            release_target()

    @app.post("/api/mutate")
    def mutate(r: GenRequest):
        p, suite = suite_for(r)
        return {"kinds": FAULT_KINDS, **mutation_matrix(p, suite), "n_tests": len(suite.tests)}

    if STATIC.exists() and (STATIC / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")

        @app.get("/{path:path}")
        def spa(path: str):
            return FileResponse(STATIC / "index.html")
    else:
        @app.get("/")
        def no_ui():
            return HTMLResponse("<p>UI not built. Run <code>npm run build</code> in frontend/.</p>"
                                "<p>API docs: <a href='/docs'>/docs</a></p>")

    return app
