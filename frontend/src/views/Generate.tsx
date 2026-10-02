import { Fragment, useEffect, useMemo, useState } from "react";
import { api, fmtMs, GenParams, GenResult, MutateResult, ProjectData, RunResult, stepText, TARGET_KIND_LABEL, Untestable } from "../api";

function UntestableRows({ items }: { items: Untestable[] }) {
  return (
    <>
      {items.map((u, i) => (
        <tr key={i} className="untestable">
          <td />
          <td className="muted">—</td>
          <td>{u.requirement_id}</td>
          <td><span className="badge untestable-badge">not testable</span></td>
          <td colSpan={4}>
            <div className="assign">
              {Object.entries(u.assignment).map(([k, v]) => <span key={k} className={v ? "cond" : "cond neg"}>{v ? "✔" : "✘"} {k}</span>)}
            </div>
            {u.note && <div className="small"><b>{u.note}</b></div>}
            <div className="small muted">because {u.reason}</div>
          </td>
        </tr>
      ))}
    </>
  );
}

type Pane = "cases" | "code" | "faults";

const paramsToText = (p: Record<string, unknown>) =>
  Object.entries(p).map(([k, v]) => `${k}=${v ?? ""}`).join("; ");
const textToParams = (s: string): Record<string, string> => Object.fromEntries(
  s.split(";").map((x) => x.trim()).filter((x) => x.includes("=")).map((x) => {
    const i = x.indexOf("=");
    return [x.slice(0, i).trim(), x.slice(i + 1).trim()];
  }),
);

export default function GenerateView({ project }: { project: ProjectData }) {
  const [strategy, setStrategy] = useState<string>("");
  const [randomN, setRandomN] = useState(4);
  const [maxCases, setMaxCases] = useState<string>("");
  const [reqFilter, setReqFilter] = useState<string[]>([]);
  const [gen, setGen] = useState<GenResult | null>(null);
  const [runs, setRuns] = useState<Record<string, RunResult>>({});
  const targets = project.config.targets;
  const [target, setTarget] = useState<string>(() => project.config.default_target ?? targets[0]?.name ?? "");
  const targetCfg = targets.find((t) => t.name === target);
  const [paramText, setParamText] = useState("");
  const [targetStatus, setTargetStatus] = useState<{ ok: boolean; text: string } | null>(null);
  useEffect(() => { setParamText(paramsToText(targetCfg?.params ?? {})); setTargetStatus(null); setRuns({}); }, [target, targetCfg]);
  const [mut, setMut] = useState<MutateResult | null>(null);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [pane, setPane] = useState<Pane>("cases");
  const [codeFile, setCodeFile] = useState<string>("");
  const [outDir, setOutDir] = useState("out");
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  const params: GenParams = useMemo(() => ({
    strategy: strategy === "random:N" ? `random:${randomN}` : strategy || null,
    max_cases: maxCases ? Number(maxCases) : null,
    requirement_ids: reqFilter.length ? reqFilter : null,
  }), [strategy, randomN, maxCases, reqFilter]);

  const doGenerate = async () => {
    setBusy("generate"); setMsg(null);
    try {
      const g = await api.generate(params);
      setGen(g); setRuns({}); setMut(null);
      setCodeFile((f) => (f && g.pytest[f] ? f : Object.keys(g.pytest).find((k) => k.startsWith("test_")) ?? ""));
    } catch (e) { setMsg({ kind: "error", text: String(e) }); } finally { setBusy(null); }
  };

  useEffect(() => { doGenerate(); /* eslint-disable-line */ }, [params]);

  const doRun = async () => {
    setBusy("run"); setMsg(null);
    try {
      setRuns({});
      const r = await api.run({ ...params, target, params: textToParams(paramText) });
      setRuns(Object.fromEntries(r.results.map((x) => [x.id, x])));
      const failed = r.results.filter((x) => x.status !== "passed").length;
      setMsg({ kind: failed ? "error" : "ok", text: `${r.target}: ${r.results.length - failed}/${r.results.length} passed` });
    } catch (e) { setMsg({ kind: "error", text: String(e) }); } finally { setBusy(null); }
  };

  const doCheck = async () => {
    setBusy("check"); setTargetStatus(null);
    try {
      const r = await api.checkTarget(target, textToParams(paramText));
      setTargetStatus({ ok: r.available, text: r.available ? "Available" : r.message });
    } catch (e) { setTargetStatus({ ok: false, text: String(e) }); } finally { setBusy(null); }
  };

  const doMutate = async () => {
    setBusy("mutate"); setMsg(null);
    try { setMut(await api.mutate(params)); setPane("faults"); }
    catch (e) { setMsg({ kind: "error", text: String(e) }); } finally { setBusy(null); }
  };

  const doExport = async () => {
    setBusy("export"); setMsg(null);
    try {
      const r = await api.exportFiles({ ...params, out_dir: outDir });
      setMsg({ kind: "ok", text: `Wrote ${r.files.length} files to ${outDir}: ${r.files.map((f) => f.split(/[\\/]/).slice(-2).join("/")).join(", ")}` });
    } catch (e) { setMsg({ kind: "error", text: String(e) }); } finally { setBusy(null); }
  };

  const docsUrl = `/api/docs.html?${new URLSearchParams({
    ...(params.strategy ? { strategy: params.strategy } : {}),
    ...(params.max_cases ? { max_cases: String(params.max_cases) } : {}),
  })}`;

  const tests = gen?.tests ?? [];
  const nPos = tests.filter((t) => t.kind === "positive").length;
  const statusCount = (s: string) => Object.values(runs).filter((r) => r.status === s).length;
  const toggleReq = (id: string) => setReqFilter((f) => (f.includes(id) ? f.filter((x) => x !== id) : [...f, id]));

  return (
    <div className="single">
      <div className="card controls">
        <div className="form-row">
          <label>Strategy
            <select value={strategy} onChange={(e) => setStrategy(e.target.value)}>
              <option value="">project default ({project.config.strategy})</option>
              <option value="all">all — every combination (2ⁿ)</option>
              <option value="mcdc">mcdc — each condition independently (n+1)</option>
              <option value="pairwise">pairwise — every pair of condition values</option>
              <option value="random:N">random — N sampled combinations</option>
              <option value="positive_only">positive_only — 1 per requirement</option>
            </select>
          </label>
          {strategy === "random:N" && <label>N<input className="num" type="number" min={1} value={randomN} onChange={(e) => setRandomN(Number(e.target.value))} /></label>}
          <label>Max cases / req<input className="num" placeholder="∞" value={maxCases} onChange={(e) => setMaxCases(e.target.value.replace(/\D/g, ""))} /></label>
          <div className="grow">
            <div className="lbl">Requirements {reqFilter.length ? `(${reqFilter.length} selected)` : "(all)"}</div>
            <div className="chips">
              {project.requirements.map((r) => (
                <button key={r.id} className={reqFilter.includes(r.id) ? "chip-btn on" : "chip-btn"} onClick={() => toggleReq(r.id)} title={r.text}>{r.id}</button>
              ))}
              {reqFilter.length > 0 && <button className="link" onClick={() => setReqFilter([])}>clear</button>}
            </div>
          </div>
        </div>
        <div className="form-row actions">
          <div className="stats">
            <div><b>{tests.length}</b> test cases</div>
            <div><b>{nPos}</b> positive · <b>{tests.length - nPos}</b> negative</div>
            <div>est. <b>{fmtMs(gen?.est_ms)}</b></div>
            {(gen?.untestable.length ?? 0) > 0 && (
              <div className="muted" title="Combinations the strategy asked for that cannot occur on the system">
                <b>{gen!.untestable.length}</b> combination{gen!.untestable.length === 1 ? "" : "s"} not testable
              </div>
            )}
            {Object.keys(runs).length > 0 && <div><span className="ok">{statusCount("passed")} passed</span> · <span className="bad">{statusCount("failed")} failed</span> · <span className="warn">{statusCount("inconclusive")} inconclusive</span></div>}
          </div>
          <div className="spacer" />
          <a className="button" href={docsUrl} target="_blank" rel="noreferrer">Open test documentation ↗</a>
          <label className="inline small">Export to<input value={outDir} onChange={(e) => setOutDir(e.target.value)} /></label>
          <button onClick={doExport} disabled={!!busy}>Export</button>
        </div>
        <div className="form-row actions target-row">
          {targets.length === 0 ? (
            <span className="muted">No test targets configured. Add one under <b>Settings → Test targets</b> to run the tests.</span>
          ) : (
            <>
              <label>Target
                <select value={target} onChange={(e) => setTarget(e.target.value)}>
                  {targets.map((t) => <option key={t.name} value={t.name}>{t.name}</option>)}
                </select>
              </label>
              <label className="grow">Parameters
                <input placeholder="key=value; key=value" value={paramText} onChange={(e) => setParamText(e.target.value)} spellCheck={false} />
              </label>
              <button onClick={doCheck} disabled={!!busy}>{busy === "check" ? "Checking…" : "Check"}</button>
              <button className="primary" onClick={doRun} disabled={!!busy}>{busy === "run" ? "Running…" : "Run tests"}</button>
            </>
          )}
        </div>
        {targetCfg && (
          <div className="target-info small">
            <span className={`kind-badge ${targetCfg.kind}`}>{TARGET_KIND_LABEL[targetCfg.kind]}</span>
            <span className="muted">{targetCfg.description}</span>
            <span className="adapters">{Object.entries(targetCfg.adapters).map(([tr, a]) => (
              <code key={tr} title={String(a.type)}>{tr}: {String(a.type ?? "").split(":").pop()}</code>
            ))}</span>
            {targetStatus && <span className={targetStatus.ok ? "ok" : "bad"}>{targetStatus.ok ? "● " : "✕ "}{targetStatus.text}</span>}
          </div>
        )}
        {msg && <div className={`banner ${msg.kind}`}>{msg.text}</div>}
        {gen?.issues.map((i, k) => <div key={k} className="banner warn small"><b>{i.requirement_id}</b> {i.message}</div>)}
      </div>

      <div className="subtabs">
        <button className={pane === "cases" ? "active" : ""} onClick={() => setPane("cases")}>Test procedures</button>
        <button className={pane === "code" ? "active" : ""} onClick={() => setPane("code")}>Executable tests (pytest)</button>
        <button className={pane === "faults" ? "active" : ""} onClick={() => setPane("faults")}>Suite check</button>
      </div>

      {pane === "cases" && (
        <div className="card flush">
          <table className="grid cases">
            <thead><tr><th /><th>Test case</th><th>Requirement</th><th>Type</th><th>Title</th><th>Also exercises</th><th className="r">Est.</th><th>Result</th></tr></thead>
            <tbody>
              {tests.map((t, idx) => {
                const run = runs[t.id];
                const lastOfReq = tests[idx + 1]?.requirement_id !== t.requirement_id;
                return (
                  <Fragment key={t.id}>
                    <tr className="clickable" onClick={() => setOpen((o) => ({ ...o, [t.id]: !o[t.id] }))}>
                      <td>{open[t.id] ? "▾" : "▸"}</td>
                      <td className="mono">{t.id}</td>
                      <td>{t.requirement_id}</td>
                      <td><span className={`badge ${t.kind}`}>{t.kind}</span></td>
                      <td>{t.title}{t.warnings.length > 0 && <span className="warn" title={t.warnings.join("\n")}> ⚠</span>}</td>
                      <td className="muted">{t.traces.slice(1).join(", ")}</td>
                      <td className="r">{fmtMs(t.est_ms)}</td>
                      <td>{run && <span className={`status ${run.status}`}>{run.status}</span>}</td>
                    </tr>
                    {open[t.id] && (
                      <tr className="detail"><td /><td colSpan={7}>
                        <div className="assign">
                          {Object.entries(t.assignment).map(([k, v]) => <span key={k} className={v ? "cond" : "cond neg"}>{v ? "✔" : "✘"} {k}</span>)}
                        </div>
                        {t.notes.map((n, i) => <div key={`n${i}`} className="muted small">Note: {n}</div>)}
                        {t.warnings.map((w, i) => <div key={i} className="warn small">⚠ {w}</div>)}
                        <table className="grid compact steps">
                          <thead><tr><th>#</th><th>Phase</th><th>Action</th><th>Expected result</th><th>Via</th><th className="r">Est.</th></tr></thead>
                          <tbody>
                            {t.steps.map((s, i) => {
                              const [a, e] = stepText(s);
                              return <tr key={i} className={`phase-${s.phase}`}><td>{i + 1}</td><td>{s.phase}</td><td>{a}</td><td>{e}</td><td className="muted small">{s.via ?? ""}</td><td className="r">{fmtMs(s.est_ms)}</td></tr>;
                            })}
                          </tbody>
                        </table>
                        {run && (
                          <div className="run-out">
                            {run.message && <div className={run.status === "failed" ? "bad" : "warn"}>{run.message}</div>}
                            <pre>{run.transcript.join("\n")}</pre>
                          </div>
                        )}
                      </td></tr>
                    )}
                    {lastOfReq && <UntestableRows items={gen!.untestable.filter((u) => u.requirement_id === t.requirement_id)} />}
                  </Fragment>
                );
              })}
              {[...new Set(gen?.untestable.map((u) => u.requirement_id))].filter((rid) => !tests.some((t) => t.requirement_id === rid)).map((rid) => (
                <UntestableRows key={rid} items={gen!.untestable.filter((u) => u.requirement_id === rid)} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {pane === "code" && gen && (
        <div className="card code-pane">
          <div className="file-list">
            {Object.keys(gen.pytest).map((f) => <button key={f} className={f === codeFile ? "active" : ""} onClick={() => setCodeFile(f)}>{f}</button>)}
          </div>
          <pre className="code">{gen.pytest[codeFile] ?? ""}</pre>
        </div>
      )}

      {pane === "faults" && (
        <div className="card">
          <p className="muted">
            Tests the <i>tests</i>, not the software: each fault is injected, one at a time, into an executable model of the
            requirements, and the generated suite is run against it. A fault no test catches means the suite has a blind spot.
            Results here say nothing about whether a target passes.
          </p>
          {!mut && <button className="primary" onClick={doMutate} disabled={!!busy}>{busy === "mutate" ? "Checking…" : "Run suite check"}</button>}
          {mut && (
            <>
              <p>
                <button onClick={doMutate} disabled={!!busy}>{busy === "mutate" ? "Checking…" : "Re-run"}</button>{" "}
                <b>{mut.n_tests}</b> tests · <span className="ok">{mut.counts.caught} caught</span> · <span className="warn">{mut.counts.flagged} flagged (inconclusive)</span> · <span className="bad">{mut.counts.MISSED} missed</span>{mut.counts.equivalent ? <> · <span className="muted" title="Faults no test could ever observe, because other requirements make the ignored condition always true">{mut.counts.equivalent} equivalent (unobservable)</span></> : null}
              </p>
              <table className="grid compact">
                <thead><tr><th>Requirement</th>{mut.kinds.map((k) => <th key={k}>{k.replace("_", " ")}</th>)}</tr></thead>
                <tbody>
                  {Object.entries(mut.rows).map(([rid, row]) => (
                    <tr key={rid}><td><b>{rid}</b></td>{mut.kinds.map((k) => <td key={k} className={`fault ${row[k]}`}>{row[k]}</td>)}</tr>
                  ))}
                </tbody>
              </table>
              <p className="muted small">
                <b>never</b>: no response · <b>late</b>: after max time · <b>early</b>: before min time · <b>stuck</b>: response always on · <b>ignore conditions</b>: responds to the trigger alone. <b>equivalent</b>: the fault can't be observed on a system that obeys the other requirements.
                Try strategy <i>positive_only</i> to see what negative tests buy you.
              </p>
            </>
          )}
        </div>
      )}
    </div>
  );
}
