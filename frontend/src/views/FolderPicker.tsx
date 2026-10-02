import { useEffect, useState } from "react";
import { api, DirListing } from "../api";

/** Modal folder browser over the server's filesystem (the server is local, so this is your disk). */
export default function FolderPicker({ title, start, mode, onPick, onClose }: {
  title: string;
  start?: string;
  mode: "workspace" | "folder";   // workspace: picking an existing workspace; folder: any folder
  onPick: (path: string) => void;
  onClose: () => void;
}) {
  const [listing, setListing] = useState<DirListing | null>(null);
  const [pathInput, setPathInput] = useState("");
  const [error, setError] = useState<string | null>(null);

  const go = (path?: string) => {
    setError(null);
    api.listDir(path).then((l) => { setListing(l); setPathInput(l.path); }).catch((e) => setError(String(e)));
  };
  useEffect(() => { go(start || undefined); }, [start]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const canSelect = listing && (mode === "folder" || listing.is_workspace);

  return (
    <div className="modal-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal" role="dialog" aria-label={title}>
        <div className="modal-head">
          <h3>{title}</h3>
          <button className="icon" onClick={onClose} title="Close">✕</button>
        </div>
        <form className="pathbar" onSubmit={(e) => { e.preventDefault(); go(pathInput); }}>
          <button type="button" disabled={!listing?.parent} onClick={() => listing?.parent && go(listing.parent)} title="Up one level">↑</button>
          <input value={pathInput} onChange={(e) => setPathInput(e.target.value)} spellCheck={false} />
          <button type="submit">Go</button>
        </form>
        <div className="roots">
          {listing && <button className="chip-btn" onClick={() => go(listing.home)}>Home</button>}
          {listing?.roots.map((r) => <button key={r} className="chip-btn" onClick={() => go(r)}>{r}</button>)}
        </div>
        {error && <div className="banner error">{error}</div>}
        <div className="dir-list">
          {listing?.dirs.length === 0 && <div className="muted small pad">No subfolders</div>}
          {listing?.dirs.map((d) => (
            <div key={d.path} className={d.is_workspace ? "dir ws" : "dir"} onDoubleClick={() => (mode === "workspace" && d.is_workspace ? onPick(d.path) : go(d.path))}>
              <button className="dir-name" onClick={() => go(d.path)}>
                <span className="folder-icon">{d.is_workspace ? "◆" : "▸"}</span>{d.name}
              </button>
              {d.is_workspace && <span className="badge">workspace</span>}
              {mode === "workspace" && d.is_workspace && <button className="primary small-btn" onClick={() => onPick(d.path)}>Open</button>}
            </div>
          ))}
        </div>
        <div className="modal-foot">
          <span className="muted small">
            {mode === "workspace"
              ? (listing?.is_workspace ? "This folder is a workspace." : "Workspaces are folders containing rdtg.yaml (marked ◆).")
              : "New workspace will be created inside this folder."}
          </span>
          <div className="spacer" />
          <button onClick={onClose}>Cancel</button>
          <button className="primary" disabled={!canSelect} onClick={() => listing && onPick(listing.path)}>
            {mode === "workspace" ? "Open this workspace" : "Select this folder"}
          </button>
        </div>
      </div>
    </div>
  );
}
