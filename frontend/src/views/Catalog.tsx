import { useEffect, useState } from "react";
import { Action, api, Catalog, ConnectionPoint, fmtValue, Message, MessageField, parseScalar, ProjectData, Value } from "../api";

const KINDS = ["discrete", "enum", "analog"];
const TRANSPORTS = ["mil1553", "ieee1394", "ethernet_udp", "ethernet_tcp", "arinc429", "can", "serial"];

const listToText = (v?: Value[] | null) => (v ?? []).map(fmtValue).join(", ");
const textToList = (s: string): Value[] | null => {
  const items = s.split(",").map((x) => x.trim()).filter(Boolean);
  // Enum values stay strings (e.g. ON/OFF) so they are not coerced to booleans.
  return items.length ? items.map((x) => (isNaN(Number(x)) ? x : Number(x))) : null;
};
const num = (s: string): number | null => (s.trim() === "" ? null : Number(s));
const mapToText = (m: Record<string, Value>) => Object.entries(m).map(([k, v]) => `${k}=${fmtValue(v)}`).join(", ");
const textToMap = (s: string): Record<string, Value> => Object.fromEntries(
  s.split(",").map((x) => x.trim()).filter((x) => x.includes("=")).map((x) => {
    const [k, v] = x.split("=");
    return [k.trim(), parseScalar(v)];
  }),
);

export default function CatalogView({ project, onSaved }: { project: ProjectData; onSaved: (p: ProjectData) => void }) {
  const [cat, setCat] = useState<Catalog>(project.catalog);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stepsText, setStepsText] = useState<Record<number, string>>({});

  useEffect(() => {
    setCat(structuredClone(project.catalog));
    setStepsText(Object.fromEntries(project.catalog.actions.map((a, i) => [i, JSON.stringify(a.steps, null, 1)])));
    setDirty(false);
  }, [project]);

  const mutate = (fn: (c: Catalog) => void) => {
    setCat((c) => { const n = structuredClone(c); fn(n); return n; });
    setDirty(true);
  };

  const save = async () => {
    setError(null);
    try {
      const c = structuredClone(cat);
      c.actions.forEach((a, i) => { if (stepsText[i] !== undefined) a.steps = JSON.parse(stepsText[i] || "[]"); });
      onSaved(await api.saveCatalog(c));
    } catch (e) {
      setError(String(e));
    }
  };

  const setCp = (i: number, patch: Partial<ConnectionPoint>) => mutate((c) => { Object.assign(c.connection_points[i], patch); });
  const setMsg = (i: number, patch: Partial<Message>) => mutate((c) => { Object.assign(c.messages[i], patch); });
  const setFld = (i: number, j: number, patch: Partial<MessageField>) => mutate((c) => { Object.assign(c.messages[i].fields[j], patch); });
  const setAct = (i: number, patch: Partial<Action>) => mutate((c) => { Object.assign(c.actions[i], patch); });

  return (
    <div className="single">
      <div className="toolbar">
        <h2>Interface catalog</h2>
        <span className="muted small">How the rig stimulates and observes the software under test. Edge weights in the dependency graph come from these costs.</span>
        <div className="spacer" />
        {dirty && <span className="dirty">Unsaved changes</span>}
        <button className="primary" disabled={!dirty} onClick={save}>Save</button>
      </div>
      {error && <div className="banner error">{error}</div>}

      <div className="card">
        <div className="card-head"><h3>Connection points</h3><span className="muted small">Discrete / analog I/O driven or read by the rig</span>
          <div className="spacer" />
          <button onClick={() => mutate((c) => { c.connection_points.push({ name: "NEW_SIGNAL", kind: "discrete", direction: "input", set_cost_ms: 10, settle_ms: 0, description: "" }); })}>+ Add</button>
        </div>
        <div className="table-wrap">
          <table className="grid">
            <thead><tr><th>Name</th><th>Kind</th><th>Dir</th><th>Values (enum)</th><th>Min</th><th>Max</th><th>Units</th><th>Initial</th><th title="time to actuate">Set ms</th><th title="time before SUT sees it">Settle ms</th><th>Description</th><th /></tr></thead>
            <tbody>
              {cat.connection_points.map((cp, i) => (
                <tr key={i}>
                  <td><input value={cp.name} onChange={(e) => setCp(i, { name: e.target.value })} /></td>
                  <td><select value={cp.kind} onChange={(e) => setCp(i, { kind: e.target.value })}>{KINDS.map((k) => <option key={k}>{k}</option>)}</select></td>
                  <td><select value={cp.direction} onChange={(e) => setCp(i, { direction: e.target.value })}><option>input</option><option>output</option></select></td>
                  <td><input disabled={cp.kind !== "enum"} value={listToText(cp.values)} onChange={(e) => setCp(i, { values: textToList(e.target.value) })} /></td>
                  <td><input className="num" disabled={cp.kind !== "analog"} value={cp.min ?? ""} onChange={(e) => setCp(i, { min: num(e.target.value) })} /></td>
                  <td><input className="num" disabled={cp.kind !== "analog"} value={cp.max ?? ""} onChange={(e) => setCp(i, { max: num(e.target.value) })} /></td>
                  <td><input className="num" value={cp.units ?? ""} onChange={(e) => setCp(i, { units: e.target.value || null })} /></td>
                  <td><input className="num" value={fmtValue(cp.initial ?? null).replace("—", "")} onChange={(e) => setCp(i, { initial: cp.kind === "enum" ? (e.target.value || null) : parseScalar(e.target.value) })} /></td>
                  <td><input className="num" disabled={cp.direction !== "input"} value={cp.set_cost_ms} onChange={(e) => setCp(i, { set_cost_ms: Number(e.target.value) })} /></td>
                  <td><input className="num" disabled={cp.direction !== "input"} value={cp.settle_ms} onChange={(e) => setCp(i, { settle_ms: Number(e.target.value) })} /></td>
                  <td><input value={cp.description} onChange={(e) => setCp(i, { description: e.target.value })} /></td>
                  <td><button className="icon" onClick={() => mutate((c) => { c.connection_points.splice(i, 1); })}>✕</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card">
        <div className="card-head"><h3>Messages</h3><span className="muted small">From the software interface control documents (SICDs), normalized across transports. Fields are referenced as <code>MESSAGE.field</code>.</span>
          <div className="spacer" />
          <button onClick={() => mutate((c) => { c.messages.push({ name: "NEW_MSG", transport: "ethernet_udp", direction: "input", address: {}, send_cost_ms: 5, fields: [], description: "" }); })}>+ Add</button>
        </div>
        {cat.messages.map((m, i) => (
          <div key={i} className="subcard">
            <div className="form-row">
              <label>Name<input value={m.name} onChange={(e) => setMsg(i, { name: e.target.value })} /></label>
              <label>Transport<select value={m.transport} onChange={(e) => setMsg(i, { transport: e.target.value })}>
                {[...new Set([...TRANSPORTS, m.transport])].map((t) => <option key={t}>{t}</option>)}</select></label>
              <label>Direction<select value={m.direction} onChange={(e) => setMsg(i, { direction: e.target.value })}><option value="input">input (rig → SUT)</option><option value="output">output (SUT → rig)</option></select></label>
              <label>Send cost ms<input className="num" value={m.send_cost_ms} onChange={(e) => setMsg(i, { send_cost_ms: Number(e.target.value) })} /></label>
              <label>Rate Hz<input className="num" value={m.rate_hz ?? ""} onChange={(e) => setMsg(i, { rate_hz: num(e.target.value) })} /></label>
              <label className="grow">Address (transport-specific JSON)
                <input defaultValue={JSON.stringify(m.address)} onBlur={(e) => { try { setMsg(i, { address: JSON.parse(e.target.value || "{}") }); } catch { setError(`Invalid address JSON for ${m.name}`); } }} />
              </label>
              <button className="icon" onClick={() => mutate((c) => { c.messages.splice(i, 1); })}>✕</button>
            </div>
            <table className="grid compact">
              <thead><tr><th>Field</th><th>Kind</th><th>Values (enum)</th><th>Min</th><th>Max</th><th>Units</th><th /></tr></thead>
              <tbody>
                {m.fields.map((f, j) => (
                  <tr key={j}>
                    <td><input value={f.name} onChange={(e) => setFld(i, j, { name: e.target.value })} /></td>
                    <td><select value={f.kind} onChange={(e) => setFld(i, j, { kind: e.target.value })}>{KINDS.map((k) => <option key={k}>{k}</option>)}</select></td>
                    <td><input disabled={f.kind !== "enum"} value={listToText(f.values)} onChange={(e) => setFld(i, j, { values: textToList(e.target.value) })} /></td>
                    <td><input className="num" disabled={f.kind !== "analog"} value={f.min ?? ""} onChange={(e) => setFld(i, j, { min: num(e.target.value) })} /></td>
                    <td><input className="num" disabled={f.kind !== "analog"} value={f.max ?? ""} onChange={(e) => setFld(i, j, { max: num(e.target.value) })} /></td>
                    <td><input className="num" value={f.units ?? ""} onChange={(e) => setFld(i, j, { units: e.target.value || null })} /></td>
                    <td><button className="icon" onClick={() => mutate((c) => { c.messages[i].fields.splice(j, 1); })}>✕</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
            <button className="link" onClick={() => mutate((c) => { c.messages[i].fields.push({ name: "field", kind: "analog" }); })}>+ field</button>
          </div>
        ))}
      </div>

      <div className="card">
        <div className="card-head"><h3>Constraints</h3><span className="muted small">Combinations that can never occur on the real system for reasons no requirement states (physics, wiring, interlocks). Test generation skips them and says why.</span>
          <div className="spacer" />
          <button onClick={() => mutate((c) => { c.constraints.push({ id: `PHYS-${c.constraints.length + 1}`, never: "", description: "" }); })}>+ Add</button>
        </div>
        <table className="grid">
          <thead><tr><th style={{ width: 110 }}>ID</th><th>Never: (conditions joined with "and")</th><th>Why</th><th /></tr></thead>
          <tbody>
            {cat.constraints.map((k, i) => (
              <tr key={i}>
                <td><input value={k.id} onChange={(e) => mutate((c) => { c.constraints[i].id = e.target.value; })} /></td>
                <td><input className="mono" placeholder="FUEL_PUMP_OK is false and FUEL_PRESSURE is greater than 60" value={k.never}
                  onChange={(e) => mutate((c) => { c.constraints[i].never = e.target.value; })} spellCheck={false} /></td>
                <td><input value={k.description} onChange={(e) => mutate((c) => { c.constraints[i].description = e.target.value; })} /></td>
                <td><button className="icon" onClick={() => mutate((c) => { c.constraints.splice(i, 1); })}>✕</button></td>
              </tr>
            ))}
            {cat.constraints.length === 0 && <tr><td colSpan={4} className="muted small">No constraints.</td></tr>}
          </tbody>
        </table>
      </div>

      <div className="card">
        <div className="card-head"><h3>Actions</h3><span className="muted small">Named multi-step procedures that put the SUT into a state. Become edges in the dependency graph with weight = cost.</span>
          <div className="spacer" />
          <button onClick={() => mutate((c) => { c.actions.push({ name: "new_action", steps: [], requires: {}, produces: {}, cost_ms: 1000, description: "" }); })}>+ Add</button>
        </div>
        {cat.actions.map((a, i) => (
          <div key={i} className="subcard">
            <div className="form-row">
              <label>Name<input value={a.name} onChange={(e) => setAct(i, { name: e.target.value })} /></label>
              <label>Cost ms<input className="num" value={a.cost_ms} onChange={(e) => setAct(i, { cost_ms: Number(e.target.value) })} /></label>
              <label className="grow">Requires (SIG=VAL, …)<input defaultValue={mapToText(a.requires)} onBlur={(e) => setAct(i, { requires: textToMap(e.target.value) })} /></label>
              <label className="grow">Produces (SIG=VAL, …)<input defaultValue={mapToText(a.produces)} onBlur={(e) => setAct(i, { produces: textToMap(e.target.value) })} /></label>
              <button className="icon" onClick={() => mutate((c) => { c.actions.splice(i, 1); })}>✕</button>
            </div>
            <label className="block">Steps (JSON: <code>{"{set: {...}}"}</code>, <code>{"{send: {message, fields}}"}</code>, <code>{"{wait_ms}"}</code>, <code>{"{wait_until: {...}, timeout_ms}"}</code>)
              <textarea className="mono" rows={4} value={stepsText[i] ?? "[]"} onChange={(e) => { setStepsText((s) => ({ ...s, [i]: e.target.value })); setDirty(true); }} />
            </label>
          </div>
        ))}
      </div>
    </div>
  );
}
