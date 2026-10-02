import { useEffect, useState } from "react";
import { api, RecentWorkspace } from "../api";
import FolderPicker from "./FolderPicker";

function ago(iso: string | null): string {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 86400 * 30) return `${Math.floor(s / 86400)} d ago`;
  return new Date(iso).toLocaleDateString();
}

function joinPath(dir: string, name: string): string {
  if (!dir) return name;
  const sep = dir.includes("\\") ? "\\" : "/";
  return dir.endsWith(sep) ? dir + name : dir + sep + name;
}

export default function Home({ onOpened }: { onOpened: () => void }) {
  const [recent, setRecent] = useState<RecentWorkspace[]>([]);
  const [openPath, setOpenPath] = useState("");
  const [newName, setNewName] = useState("");
  const [newParent, setNewParent] = useState("");
  const [picker, setPicker] = useState<null | "open" | "parent">(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.recent().then(setRecent).catch(() => {});
    api.listDir().then((l) => setNewParent((p) => p || l.home + (l.home.includes("\\") ? "\\Documents" : ""))).catch(() => {});
  }, []);

  const folderName = newName.trim().replace(/[<>:"/\\|?*]+/g, "").replace(/\s+/g, "_");
  const newPath = folderName ? joinPath(newParent.trim(), folderName) : "";

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true); setError(null);
    try { await fn(); onOpened(); } catch (e) { setError(String(e).replace(/^Error: \d+: /, "")); } finally { setBusy(false); }
  };
  const open = (path: string) => run(() => api.openWorkspace(path));
  const create = () => run(() => api.createWorkspace(newPath, newName.trim()));

  return (
    <div className="home">
      <div className="home-hero">
        <span className="logo big">RDTG</span>
        <div>
          <h1>Requirements-Driven Test Generation</h1>
          <p className="muted">Write requirements, connect them to your rig's interfaces, and generate test procedures and executable tests.</p>
        </div>
      </div>
      {error && <div className="banner error">{error}</div>}
      <div className="home-grid">
        <section className="card">
          <h3>Recent workspaces</h3>
          {recent.length === 0 && <p className="muted small">No recent workspaces yet. Open or create one to get started.</p>}
          <ul className="recent">
            {recent.map((r) => (
              <li key={r.path} className={r.exists ? "" : "missing"}>
                <button className="recent-open" disabled={!r.exists || busy} onClick={() => open(r.path)}
                  title={r.exists ? `Open ${r.path}` : "Folder no longer exists"}>
                  <span className="recent-name">{r.name}</span>
                  <span className="recent-path">{r.path}</span>
                </button>
                <span className="muted small">{r.exists ? ago(r.opened) : "missing"}</span>
                <button className="icon" title="Remove from list" onClick={() => api.forget(r.path).then(setRecent)}>✕</button>
              </li>
            ))}
          </ul>
        </section>

        <div className="home-side">
          <section className="card">
            <h3>Open workspace</h3>
            <p className="muted small">A workspace is a folder containing <code>rdtg.yaml</code>, <code>catalog.yaml</code> and <code>requirements.yaml</code>.</p>
            <form className="form-row" onSubmit={(e) => { e.preventDefault(); if (openPath.trim()) open(openPath.trim()); }}>
              <input className="grow" placeholder="C:\path\to\workspace" value={openPath} onChange={(e) => setOpenPath(e.target.value)} spellCheck={false} />
              <button type="button" onClick={() => setPicker("open")}>Browse…</button>
              <button type="submit" className="primary" disabled={!openPath.trim() || busy}>Open</button>
            </form>
          </section>

          <section className="card">
            <h3>New workspace</h3>
            <form className="form" onSubmit={(e) => { e.preventDefault(); if (newPath) create(); }}>
              <label>Name<input placeholder="e.g. Brake Controller" value={newName} onChange={(e) => setNewName(e.target.value)} /></label>
              <label>Location
                <div className="form-row">
                  <input className="grow" value={newParent} onChange={(e) => setNewParent(e.target.value)} spellCheck={false} />
                  <button type="button" onClick={() => setPicker("parent")}>Browse…</button>
                </div>
              </label>
              {newPath && <div className="muted small">Will create <code>{newPath}</code></div>}
              <div><button type="submit" className="primary" disabled={!newPath || busy}>Create workspace</button></div>
            </form>
          </section>
        </div>
      </div>

      {picker === "open" && (
        <FolderPicker title="Open workspace" mode="workspace" start={openPath || undefined}
          onClose={() => setPicker(null)} onPick={(p) => { setPicker(null); setOpenPath(p); open(p); }} />
      )}
      {picker === "parent" && (
        <FolderPicker title="Choose location for the new workspace" mode="folder" start={newParent || undefined}
          onClose={() => setPicker(null)} onPick={(p) => { setPicker(null); setNewParent(p); }} />
      )}
    </div>
  );
}
