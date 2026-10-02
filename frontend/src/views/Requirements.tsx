import { useEffect, useMemo, useRef, useState } from "react";
import { api, fmtCond, fmtMs, fmtValue, Issue, ProjectData, RawRequirement, Requirement } from "../api";

interface Draft extends RawRequirement { key: number }
interface Preview { requirement: Requirement; issues: Issue[] }

const TEMPLATES: [string, string][] = [
  ["Event", "When <TRIGGER> is <VALUE>, the software shall set <OUTPUT> to <VALUE> within <N> ms."],
  ["Event + condition", "When <TRIGGER> is <VALUE> and <SIGNAL> is <VALUE>, the software shall set <OUTPUT> to <VALUE> within <N> ms."],
  ["State", "While <STATE> is <VALUE>, the software shall set <OUTPUT> to <VALUE>."],
  ["State + event", "While <STATE> is <VALUE>, when <TRIGGER> is <VALUE>, the software shall set <OUTPUT> to <VALUE> within <N> ms."],
  ["Unwanted", "If <SIGNAL> is less than <N>, then the software shall set <OUTPUT> to true within <N> ms."],
  ["Timing window", "When <TRIGGER> is <VALUE>, the software shall set <OUTPUT> to <VALUE> between <MIN> and <MAX> ms."],
];

const PATTERN_LABEL: Record<string, string> = {
  event: "Event-driven", state: "State-driven", complex: "Complex", unwanted: "Unwanted behaviour",
  optional: "Optional feature", ubiquitous: "Ubiquitous",
};

let nextKey = 1;

function nextId(drafts: Draft[]): string {
  const nums = drafts.map((d) => /(\d+)\s*$/.exec(d.id)).filter(Boolean).map((m) => Number(m![1]));
  const prefix = drafts.length ? drafts[drafts.length - 1].id.replace(/\d+\s*$/, "") : "SRS-";
  const n = (nums.length ? Math.max(...nums) : 0) + 1;
  return `${prefix}${String(n).padStart(3, "0")}`;
}

export default function RequirementsView({ project, onSaved }: { project: ProjectData; onSaved: (p: ProjectData) => void }) {
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [previews, setPreviews] = useState<Record<number, Preview>>({});
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const focused = useRef<{ key: number; el: HTMLTextAreaElement } | null>(null);
  const timers = useRef<Record<number, number>>({});

  // Initialise drafts + previews from the saved project.
  useEffect(() => {
    const ds = project.raw_requirements.map((r) => ({ ...r, key: nextKey++ }));
    const pv: Record<number, Preview> = {};
    ds.forEach((d, i) => {
      const req = project.requirements[i];
      if (req) pv[d.key] = { requirement: req, issues: project.issues.filter((x) => x.requirement_id === req.id) };
    });
    setDrafts(ds);
    setPreviews(pv);
    setDirty(false);
  }, [project]);

  const schedulePreview = (d: Draft) => {
    window.clearTimeout(timers.current[d.key]);
    timers.current[d.key] = window.setTimeout(() => {
      api.parse(d.id, d.text).then((res) => setPreviews((p) => ({ ...p, [d.key]: res }))).catch(() => {});
    }, 350);
  };

  const update = (key: number, patch: Partial<Draft>) => {
    setDrafts((ds) => ds.map((d) => {
      if (d.key !== key) return d;
      const nd = { ...d, ...patch };
      if (patch.text !== undefined || patch.id !== undefined) schedulePreview(nd);
      return nd;
    }));
    setDirty(true);
  };

  const add = (text = "") => {
    const d: Draft = { key: nextKey++, id: nextId(drafts), text };
    setDrafts((ds) => [...ds, d]);
    if (text) schedulePreview(d);
    setDirty(true);
    setTimeout(() => document.getElementById(`req-${d.key}`)?.focus(), 0);
  };

  const remove = (key: number) => {
    setDrafts((ds) => ds.filter((d) => d.key !== key));
    setDirty(true);
  };

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const raw = drafts.map(({ key: _k, ...r }) => r);
      onSaved(await api.saveRequirements(raw));
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  };

  const insertSignal = (name: string) => {
    const f = focused.current;
    if (!f) return;
    const { el, key } = f;
    const start = el.selectionStart ?? el.value.length;
    const end = el.selectionEnd ?? start;
    const text = el.value.slice(0, start) + name + el.value.slice(end);
    update(key, { text });
    requestAnimationFrame(() => {
      el.focus();
      el.setSelectionRange(start + name.length, start + name.length);
    });
  };

  const signals = useMemo(() => Object.values(project.signals), [project.signals]);
  const shown = drafts.filter((d) => !filter || `${d.id} ${d.text}`.toLowerCase().includes(filter.toLowerCase()));
  const dupIds = new Set(drafts.map((d) => d.id).filter((id, i, a) => a.indexOf(id) !== i));

  return (
    <div className="split">
      <section className="main-col">
        <div className="toolbar">
          <h2>Requirements <span className="muted">({drafts.length})</span></h2>
          <input className="search" placeholder="Filter…" value={filter} onChange={(e) => setFilter(e.target.value)} />
          <div className="spacer" />
          {dirty && <span className="dirty">Unsaved changes</span>}
          <button onClick={() => add()}>+ Add</button>
          <button className="primary" disabled={!dirty || saving} onClick={save}>{saving ? "Saving…" : "Save"}</button>
        </div>
        {error && <div className="banner error">{error}</div>}
        <div className="templates">
          <span className="muted">New from EARS template:</span>
          {TEMPLATES.map(([label, t]) => <button key={label} className="chip-btn" onClick={() => add(t)}>{label}</button>)}
        </div>
        {shown.map((d) => (
          <RequirementCard
            key={d.key} draft={d} preview={previews[d.key]} strategies={project.strategies}
            duplicate={dupIds.has(d.id)}
            onChange={(patch) => update(d.key, patch)} onRemove={() => remove(d.key)}
            onFocus={(el) => { focused.current = { key: d.key, el }; }}
          />
        ))}
        {drafts.length === 0 && <div className="empty">No requirements yet. Add one or start from a template.</div>}
      </section>
      <aside className="side-col">
        <h3>Signals</h3>
        <p className="muted small">Click to insert at the cursor.</p>
        <div className="signal-list">
          {signals.length === 0 && <p className="muted small">No signals yet. Add connection points and messages on the <b>Interfaces</b> tab, then reference them by name here.</p>}
          {signals.map((s) => (
            <button key={s.name} className="signal" onClick={() => insertSignal(s.name)} title={describeSignal(s)}>
              <span className={`dir ${s.direction}`}>{s.direction === "input" ? "IN" : "OUT"}</span>
              <span className="sname">{s.name}</span>
              <span className="muted small">{s.values ? s.values.map(fmtValue).join("|") : s.kind === "analog" ? `${s.min ?? ""}…${s.max ?? ""}${s.units ? " " + s.units : ""}` : "bool"}</span>
            </button>
          ))}
        </div>
        <h3>Syntax</h3>
        <div className="small syntax">
          <p><b>Conditions</b>: <code>X is V</code>, <code>X is not V</code>, <code>X is greater than N</code>, <code>X is at least N</code>, <code>X &lt; N</code>, <code>X is set</code></p>
          <p><b>Responses</b>: <code>set X to V</code>, <code>set X</code>, <code>clear X</code>, <code>report X as V</code></p>
          <p><b>Timing</b>: <code>within N ms</code>, <code>between A and B ms</code>, <code>no earlier than N s</code></p>
        </div>
      </aside>
    </div>
  );
}

function describeSignal(s: ProjectData["signals"][string]) {
  const src = s.source === "message" ? `${s.message} via ${s.transport}` : "connection point";
  return `${s.name} — ${s.kind} ${s.direction} (${src}), set cost ${s.set_cost_ms} ms`;
}

function RequirementCard({ draft, preview, strategies, duplicate, onChange, onRemove, onFocus }: {
  draft: Draft; preview?: Preview; strategies: string[]; duplicate: boolean;
  onChange: (p: Partial<Draft>) => void; onRemove: () => void; onFocus: (el: HTMLTextAreaElement) => void;
}) {
  const r = preview?.requirement;
  const issues = [...(preview?.issues ?? [])];
  if (duplicate) issues.unshift({ level: "error", code: "DUPLICATE_ID", message: "duplicate id", requirement_id: draft.id });
  const worst = issues.some((i) => i.level === "error") ? "error" : issues.some((i) => i.level === "warning") ? "warning" : "ok";

  return (
    <div className={`card req-card ${worst}`}>
      <div className="req-head">
        <input className="req-id" value={draft.id} onChange={(e) => onChange({ id: e.target.value })} />
        {r?.parsed && r.pattern && <span className="badge pattern">{PATTERN_LABEL[r.pattern] ?? r.pattern}</span>}
        <div className="spacer" />
        <label className="small" title="Confirm that having no timing bound is intentional">
          <input type="checkbox" checked={!!draft.timing_ack} onChange={(e) => onChange({ timing_ack: e.target.checked || undefined })} /> untimed OK
        </label>
        <select value={draft.strategy ?? ""} onChange={(e) => onChange({ strategy: e.target.value || undefined })} title="Test strategy override">
          <option value="">default strategy</option>
          {strategies.filter((s) => s !== "random:N").map((s) => <option key={s} value={s}>{s}</option>)}
          <option value="random:4">random:4</option>
        </select>
        <button className="icon" title="Delete" onClick={onRemove}>✕</button>
      </div>
      <textarea
        id={`req-${draft.key}`}
        rows={2}
        value={draft.text}
        placeholder="When <trigger>, the software shall <response> within <N> ms."
        onChange={(e) => onChange({ text: e.target.value })}
        onFocus={(e) => onFocus(e.currentTarget)}
      />
      {r?.parsed && (
        <div className="parsed">
          {r.conditions.length > 0 && (
            <div className="prow"><span className="kw">{r.pattern === "state" || r.pattern === "complex" || r.pattern === "optional" ? "WHILE" : "AND"}</span>
              {r.conditions.map((c, i) => <span key={i} className="cond">{fmtCond(c)}</span>)}</div>
          )}
          {r.trigger && (
            <div className="prow"><span className="kw">{r.pattern === "unwanted" ? "IF" : "WHEN"}</span><span className="cond trig">{fmtCond(r.trigger)}</span></div>
          )}
          <div className="prow"><span className="kw">SHALL</span>
            {r.response.map((c, i) => <span key={i} className="cond resp">{fmtCond(c)}</span>)}</div>
          <div className="prow"><span className="kw">TIMING</span>
            <span className={r.timing ? "cond time" : "muted small"}>
              {r.timing ? [r.timing.min_ms != null ? `≥ ${fmtMs(r.timing.min_ms)}` : "", r.timing.max_ms != null ? `≤ ${fmtMs(r.timing.max_ms)}` : ""].filter(Boolean).join(", ") : "none"}
            </span></div>
        </div>
      )}
      {issues.length > 0 && (
        <ul className="issues">
          {issues.map((i, k) => <li key={k} className={i.level}><b>{i.code}</b> {i.message}</li>)}
        </ul>
      )}
    </div>
  );
}
