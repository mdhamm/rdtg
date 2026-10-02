import { useEffect, useMemo, useState } from "react";
import { api, Config, ProjectData, TARGET_KIND_LABEL, TargetConfig, TargetKind } from "../api";

const STRATEGIES = ["all", "mcdc", "pairwise", "random:4", "random:8", "positive_only"];

const paramsToText = (p: Record<string, unknown>) => Object.entries(p).map(([k, v]) => `${k}=${v ?? ""}`).join("; ");
const textToParams = (s: string): Record<string, string> => Object.fromEntries(
  s.split(";").map((x) => x.trim()).filter((x) => x.includes("=")).map((x) => {
    const i = x.indexOf("=");
    return [x.slice(0, i).trim(), x.slice(i + 1).trim()];
  }),
);

export default function SettingsView({ project, onSaved }: { project: ProjectData; onSaved: (p: ProjectData) => void }) {
  const [cfg, setCfg] = useState<Config>(project.config);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [providers, setProviders] = useState<string[]>([]);

  useEffect(() => { setCfg(structuredClone(project.config)); setDirty(false); }, [project]);
  useEffect(() => {
    api.graph().then((g) => {
      const ids = g.nodes.filter((n) => n.type !== "fact").map((n) => n.id.replace(/^p:/, ""));
      setProviders([...new Set([...ids, ...project.config.disallowed_providers])].sort());
    }).catch(() => {});
  }, [project]);

  // Interfaces the catalog needs an adapter for: connection points use "discrete".
  const transports = useMemo(() => {
    const t = new Set<string>();
    if (project.catalog.connection_points.length) t.add("discrete");
    project.catalog.messages.forEach((m) => t.add(m.transport));
    return [...t];
  }, [project.catalog]);

  const set = (patch: Partial<Config>) => { setCfg((c) => ({ ...c, ...patch })); setDirty(true); };
  const setTarget = (i: number, patch: Partial<TargetConfig>) => {
    setCfg((c) => {
      const targets = c.targets.map((t, j) => (j === i ? { ...t, ...patch } : t));
      const renamed = patch.name !== undefined && c.default_target === c.targets[i].name;
      return { ...c, targets, default_target: renamed ? patch.name! : c.default_target };
    });
    setDirty(true);
  };
  const addTarget = () => {
    const name = `Target ${cfg.targets.length + 1}`;
    set({
      targets: [...cfg.targets, {
        name, kind: "hardware", description: "", start: null, params: {},
        adapters: Object.fromEntries(transports.map((t) => [t, { type: "" }])),
      }],
      default_target: cfg.default_target ?? name,
    });
  };
  const removeTarget = (i: number) => {
    const targets = cfg.targets.filter((_, j) => j !== i);
    set({ targets, default_target: cfg.default_target === cfg.targets[i].name ? (targets[0]?.name ?? null) : cfg.default_target });
  };

  const save = async () => {
    setError(null);
    const names = cfg.targets.map((t) => t.name.trim());
    if (names.some((n) => !n)) return setError("Every target needs a name.");
    if (new Set(names.map((n) => n.toLowerCase())).size !== names.length) return setError("Target names must be unique.");
    try { onSaved(await api.saveConfig(cfg)); } catch (e) { setError(String(e)); }
  };
  const toggleProvider = (id: string) => set({
    disallowed_providers: cfg.disallowed_providers.includes(id)
      ? cfg.disallowed_providers.filter((x) => x !== id) : [...cfg.disallowed_providers, id],
  });

  return (
    <div className="single narrow wide-ish">
      <div className="toolbar">
        <h2>Workspace settings</h2>
        <div className="spacer" />
        {dirty && <span className="dirty">Unsaved changes</span>}
        <button className="primary" disabled={!dirty} onClick={save}>Save</button>
      </div>
      {error && <div className="banner error">{error}</div>}

      <div className="card form">
        <h3>Test generation</h3>
        <label>Workspace name<input value={cfg.name} onChange={(e) => set({ name: e.target.value })} /></label>
        <label>Default timeout (ms)
          <input className="num" type="number" value={cfg.default_timeout_ms} onChange={(e) => set({ default_timeout_ms: Number(e.target.value) })} />
          <span className="hint">Used when a requirement has no timing bound (and as its edge weight in the graph).</span>
        </label>
        <label>Negative observation window factor
          <input className="num" type="number" step="0.1" value={cfg.negative_window_factor} onChange={(e) => set({ negative_window_factor: Number(e.target.value) })} />
          <span className="hint">Negative tests watch for the response for max_ms × this factor.</span>
        </label>
        <label>Default strategy
          <select value={cfg.strategy} onChange={(e) => set({ strategy: e.target.value })}>
            {STRATEGIES.map((s) => <option key={s}>{s}</option>)}
            {!STRATEGIES.includes(cfg.strategy) && <option>{cfg.strategy}</option>}
          </select>
        </label>
        <label>Max cases per requirement
          <input className="num" placeholder="unlimited" value={cfg.max_cases_per_requirement ?? ""} onChange={(e) => set({ max_cases_per_requirement: e.target.value ? Number(e.target.value) : null })} />
        </label>
        <label>Random seed<input className="num" type="number" value={cfg.random_seed} onChange={(e) => set({ random_seed: Number(e.target.value) })} /></label>
        <div className="block">
          <div className="lbl">Disallowed setup paths</div>
          <span className="hint">Paths the planner must not use to establish preconditions (e.g. forcing a signal that is not allowed in qualification testing).</span>
          <div className="chips">
            {providers.map((p) => (
              <button key={p} className={cfg.disallowed_providers.includes(p) ? "chip-btn off" : "chip-btn"} onClick={() => toggleProvider(p)}>
                {cfg.disallowed_providers.includes(p) ? "⊘ " : ""}{p}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <h3>Test targets</h3>
          <div className="spacer" />
          <button onClick={addTarget}>+ Add target</button>
        </div>
        <p className="hint">
          A target is a configuration the generated tests can run against: it picks an adapter for every interface
          the catalog uses{transports.length ? <> (<b>{transports.join(", ")}</b>)</> : null}. Mixing emulated and real adapters gives a partially
          simulated target. Adapter types are <code>rig/file.py:Class</code> (relative to the workspace) or <code>package.module:Class</code>;
          other fields are passed to the adapter's constructor.
        </p>
        {cfg.targets.length === 0 && <div className="empty">No targets yet.</div>}
        {cfg.targets.map((t, i) => (
          <TargetCard key={i} target={t} isDefault={cfg.default_target === t.name} transports={transports}
            onChange={(patch) => setTarget(i, patch)} onRemove={() => removeTarget(i)}
            onMakeDefault={() => set({ default_target: t.name })} />
        ))}
      </div>
    </div>
  );
}

function TargetCard({ target, isDefault, transports, onChange, onRemove, onMakeDefault }: {
  target: TargetConfig; isDefault: boolean; transports: string[];
  onChange: (p: Partial<TargetConfig>) => void; onRemove: () => void; onMakeDefault: () => void;
}) {
  const [paramText, setParamText] = useState(paramsToText(target.params));
  const rows = Object.entries(target.adapters);
  const missing = transports.filter((tr) => !target.adapters[tr] || !target.adapters[tr].type);

  const setAdapter = (oldKey: string, key: string, spec: Record<string, unknown>) => {
    const entries = Object.entries(target.adapters).map(([k, v]) => (k === oldKey ? [key, spec] : [k, v]));
    onChange({ adapters: Object.fromEntries(entries) });
  };
  const options = (spec: Record<string, unknown>) => {
    const { type: _t, ...rest } = spec;
    return Object.keys(rest).length ? JSON.stringify(rest) : "";
  };

  return (
    <div className={isDefault ? "target-card default" : "target-card"}>
      <div className="form-row">
        <label className="grow">Name<input value={target.name} onChange={(e) => onChange({ name: e.target.value })} /></label>
        <label>Kind
          <select value={target.kind} onChange={(e) => onChange({ kind: e.target.value as TargetKind })}>
            {(Object.keys(TARGET_KIND_LABEL) as TargetKind[]).map((k) => <option key={k} value={k}>{TARGET_KIND_LABEL[k]}</option>)}
          </select>
        </label>
        {isDefault ? <span className="badge">default</span> : <button onClick={onMakeDefault}>Make default</button>}
        <button className="icon" title="Delete target" onClick={onRemove}>✕</button>
      </div>
      <label className="block">Description<input style={{ width: "100%" }} value={target.description} onChange={(e) => onChange({ description: e.target.value })} /></label>
      <div className="form-row">
        <label className="grow">Start hook (optional)
          <input placeholder="rig/file.py:function — e.g. launch the software under test" value={target.start ?? ""} onChange={(e) => onChange({ start: e.target.value || null })} spellCheck={false} />
        </label>
        <label className="grow">Default parameters
          <input placeholder="key=value; key=value" value={paramText} onChange={(e) => setParamText(e.target.value)}
            onBlur={() => onChange({ params: textToParams(paramText) })} spellCheck={false} />
        </label>
      </div>
      <table className="grid compact">
        <thead><tr><th>Interface</th><th>Adapter type</th><th>Options (JSON)</th><th /></tr></thead>
        <tbody>
          {rows.map(([tr, spec]) => (
            <tr key={tr}>
              <td><input value={tr} onChange={(e) => setAdapter(tr, e.target.value, spec)} /></td>
              <td><input className="mono" value={String(spec.type ?? "")} placeholder="rig/file.py:Class" spellCheck={false}
                onChange={(e) => setAdapter(tr, tr, { ...spec, type: e.target.value })} /></td>
              <td><input className="mono" defaultValue={options(spec)} placeholder='{"host": "127.0.0.1"}' spellCheck={false}
                onBlur={(e) => {
                  try {
                    const extra = e.target.value.trim() ? JSON.parse(e.target.value) : {};
                    setAdapter(tr, tr, { type: spec.type, ...extra });
                    e.target.classList.remove("invalid");
                  } catch { e.target.classList.add("invalid"); }
                }} /></td>
              <td><button className="icon" onClick={() => onChange({ adapters: Object.fromEntries(rows.filter(([k]) => k !== tr)) })}>✕</button></td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="form-row">
        <button className="link" onClick={() => {
          const next = transports.find((x) => !target.adapters[x]) ?? `interface${rows.length + 1}`;
          onChange({ adapters: { ...target.adapters, [next]: { type: "" } } });
        }}>+ adapter</button>
        {missing.length > 0 && <span className="warn small">⚠ No adapter for: {missing.join(", ")}. Tests using those signals will be inconclusive.</span>}
      </div>
    </div>
  );
}
