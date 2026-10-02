import { useCallback, useEffect, useState } from "react";
import { api, ProjectData, WorkspaceInfo } from "./api";
import RequirementsView from "./views/Requirements";
import CatalogView from "./views/Catalog";
import GraphView from "./views/Graph";
import GenerateView from "./views/Generate";
import SettingsView from "./views/Settings";
import Home from "./views/Home";

const TABS = [
  ["requirements", "Requirements"],
  ["catalog", "Interfaces"],
  ["graph", "Dependency Graph"],
  ["generate", "Generate & Run"],
  ["settings", "Settings"],
] as const;
type Tab = (typeof TABS)[number][0];

export default function App() {
  const [workspace, setWorkspace] = useState<WorkspaceInfo | null>(null);
  const [project, setProject] = useState<ProjectData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>(() => {
    try { return (localStorage.getItem("rdtg.tab") as Tab) || "requirements"; } catch { return "requirements"; }
  });

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const w = await api.workspace();
      setWorkspace(w);
      setProject(w.open ? await api.project() : null);
    } catch (e) {
      setError(String(e));
    }
  }, []);
  useEffect(() => { refresh(); }, [refresh]);
  useEffect(() => { try { localStorage.setItem("rdtg.tab", tab); } catch { /* ignore */ } }, [tab]);
  useEffect(() => {
    document.title = project ? `${project.config.name} — RDTG` : "RDTG — Requirements-Driven Test Generation";
  }, [project]);

  const closeWorkspace = async () => {
    await api.closeWorkspace().catch(() => {});
    refresh();
  };

  const errors = project?.issues.filter((i) => i.level === "error").length ?? 0;
  const warnings = project?.issues.filter((i) => i.level === "warning").length ?? 0;

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="logo">RDTG</span>
          {project ? (
            <div>
              <div className="title">{project.config.name}</div>
              <div className="subtitle" title={project.root}>{project.root}</div>
            </div>
          ) : (
            <div className="title">Requirements-Driven Test Generation</div>
          )}
        </div>
        {project && (
          <>
            <nav className="tabs">
              {TABS.map(([id, label]) => (
                <button key={id} className={tab === id ? "tab active" : "tab"} onClick={() => setTab(id)}>
                  {label}
                  {id === "requirements" && (errors > 0 || warnings > 0) && (
                    <span className={errors ? "count err" : "count warn"}>{errors || warnings}</span>
                  )}
                </button>
              ))}
            </nav>
            <div className="spacer" />
            <button className="ghost" onClick={closeWorkspace} title="Close this workspace and return to the workspace list">
              Switch workspace
            </button>
          </>
        )}
      </header>
      {error && <div className="banner error">{error} <button onClick={refresh}>Retry</button></div>}
      <main className="content">
        {workspace && !workspace.open && <Home onOpened={refresh} />}
        {project && tab === "requirements" && <RequirementsView project={project} onSaved={setProject} />}
        {project && tab === "catalog" && <CatalogView project={project} onSaved={setProject} />}
        {project && tab === "graph" && <GraphView project={project} />}
        {project && tab === "generate" && <GenerateView project={project} />}
        {project && tab === "settings" && <SettingsView project={project} onSaved={setProject} />}
      </main>
    </div>
  );
}
