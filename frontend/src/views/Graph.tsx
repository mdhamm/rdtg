import cytoscape, { Core } from "cytoscape";
import dagre from "cytoscape-dagre";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, fmtMs, GraphData, GraphNode, PlanResult, ProjectData, stepText, Value } from "../api";

cytoscape.use(dagre);

const COLORS: Record<string, string> = {
  fact: "#ffffff", requirement: "#2457c5", action: "#7b3fb8", set: "#6b7487", send: "#2c8a7a",
};

function parseFactId(id: string): { signal: string; op: string; value: Value } | null {
  const m = /^f:(.+?)(==|!=|<=|>=|<|>)(.*)$/.exec(id);
  if (!m) return null;
  const raw = m[3];
  const value: Value = raw === "true" ? true : raw === "false" ? false : raw !== "" && !isNaN(Number(raw)) ? Number(raw) : raw;
  return { signal: m[1], op: m[2], value };
}

export default function GraphView({ project }: { project: ProjectData }) {
  const [data, setData] = useState<GraphData | null>(null);
  const [selected, setSelected] = useState<GraphNode | null>(null);
  const [plan, setPlan] = useState<PlanResult | null>(null);
  const [showRig, setShowRig] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const cy = useRef<Core | null>(null);

  useEffect(() => { api.graph().then(setData).catch((e) => setError(String(e))); }, [project]);

  const elements = useMemo(() => {
    if (!data) return [];
    const hidden = new Set(showRig ? [] : data.nodes.filter((n) => n.type === "set" || n.type === "send").map((n) => n.id));
    return [
      ...data.nodes.filter((n) => !hidden.has(n.id)).map((n) => ({
        data: { ...n, display: n.type === "fact" ? n.label : `${n.label}\n${fmtMs(n.cost)}` },
        classes: [n.type, n.type === "fact" && n.reachable === false ? "unreachable" : ""].join(" "),
      })),
      ...data.edges.filter((e) => !hidden.has(e.source) && !hidden.has(e.target)).map((e, i) => ({
        data: { id: `e${i}`, ...e, display: e.kind === "produces" ? fmtMs(e.weight) : "" },
        classes: e.kind,
      })),
    ];
  }, [data, showRig]);

  useEffect(() => {
    if (!box.current || !data) return;
    const inst = cytoscape({
      container: box.current,
      elements,
      wheelSensitivity: 0.3,
      style: [
        { selector: "node", style: { label: "data(display)", "font-size": 12, "text-wrap": "wrap", "text-valign": "center", "text-halign": "center", color: "#fff", "background-color": "#888", shape: "round-rectangle", width: "label", height: "label", padding: "8px", "font-family": "system-ui, sans-serif" } },
        { selector: "node.fact", style: { "background-color": COLORS.fact, color: "#1d2433", "border-width": 1.5, "border-color": "#9aa4b8", shape: "ellipse", padding: "10px" } },
        { selector: "node.fact.unreachable", style: { "border-color": "#c0392b", "border-style": "dashed", color: "#c0392b" } },
        { selector: "node.requirement", style: { "background-color": COLORS.requirement, "font-weight": "bold" } },
        { selector: "node.action", style: { "background-color": COLORS.action } },
        { selector: "node.set", style: { "background-color": COLORS.set, "font-size": 10, shape: "cut-rectangle" } },
        { selector: "node.send", style: { "background-color": COLORS.send, "font-size": 10, shape: "cut-rectangle" } },
        { selector: "edge", style: { width: 1.5, "curve-style": "bezier", "target-arrow-shape": "triangle", "line-color": "#b8c0cf", "target-arrow-color": "#b8c0cf", label: "data(display)", "font-size": 9, color: "#5b6578", "text-background-color": "#fff", "text-background-opacity": 1, "text-background-padding": "2px" } },
        { selector: "edge.requires", style: { "line-style": "dashed" } },
        { selector: ".dim", style: { opacity: 0.18 } },
        { selector: ".hl", style: { opacity: 1 } },
        { selector: "edge.hl", style: { "line-color": "#e08a00", "target-arrow-color": "#e08a00", width: 3 } },
        { selector: "node.hl", style: { "border-width": 3, "border-color": "#e08a00" } },
        { selector: "node:selected", style: { "border-width": 3, "border-color": "#e08a00" } },
      ],
      layout: { name: "dagre", rankDir: "TB", nodeSep: 18, rankSep: 55, edgeSep: 8, padding: 20 } as cytoscape.LayoutOptions,
    });
    inst.on("tap", "node", (evt) => {
      const n = evt.target.data() as GraphNode;
      setSelected(n);
    });
    inst.on("tap", (evt) => { if (evt.target === inst) { setSelected(null); setPlan(null); } });
    cy.current = inst;
    return () => inst.destroy();
  }, [elements, data]);

  // Plan + highlight when a fact is selected.
  useEffect(() => {
    const inst = cy.current;
    if (!inst) return;
    inst.elements().removeClass("dim hl");
    setPlan(null);
    if (!selected) return;
    if (selected.type === "fact") {
      const cond = parseFactId(selected.id);
      if (!cond) return;
      api.plan(cond).then((p) => {
        setPlan(p);
        const used = new Set(p.used.map((u) => `p:${u}`));
        const touched = new Set(p.steps.filter((s) => s.signal).map((s) => s.signal as string));
        inst.elements().addClass("dim");
        inst.nodes().forEach((n) => {
          const d = n.data() as GraphNode;
          if (used.has(d.id) || d.id === selected.id || (d.type === "fact" && d.signal && touched.has(d.signal))) n.removeClass("dim").addClass("hl");
          if ((d.type === "set" || d.type === "send") && touched.has(d.label.replace("rig: ", ""))) n.removeClass("dim").addClass("hl");
        });
        inst.edges().forEach((e) => {
          if (e.source().hasClass("hl") && e.target().hasClass("hl")) e.removeClass("dim").addClass("hl");
        });
      }).catch((e) => setError(String(e)));
    } else {
      const n = inst.getElementById(selected.id);
      inst.elements().addClass("dim");
      n.closedNeighborhood().removeClass("dim").addClass("hl");
    }
  }, [selected]);

  const reqText = selected?.type === "requirement" ? project.requirements.find((r) => r.id === selected.label)?.text : null;

  return (
    <div className="split">
      <section className="main-col graph-col">
        <div className="toolbar">
          <h2>Dependency graph</h2>
          <span className="legend">
            <i className="lg fact" /> fact <i className="lg requirement" /> requirement (SUT) <i className="lg action" /> action <i className="lg set" /> rig input <i className="lg send" /> message
          </span>
          <div className="spacer" />
          <label className="small"><input type="checkbox" checked={showRig} onChange={(e) => setShowRig(e.target.checked)} /> show rig inputs</label>
          <button onClick={() => cy.current?.fit(undefined, 20)}>Fit</button>
        </div>
        {error && <div className="banner error">{error}</div>}
        <div className="graph" ref={box} />
        <p className="muted small">Edges are labelled with their time cost. Dashed edges are preconditions (a requirement or action needs <i>all</i> of them). Click a fact to see the cheapest way to establish it.</p>
      </section>
      <aside className="side-col">
        {!selected && (
          <>
            <h3>Setup cost per requirement</h3>
            <p className="muted small">Cheapest time to satisfy / violate each stimulus.</p>
            {data && Object.entries(data.setup_costs).map(([rid, rows]) => (
              <div key={rid} className="cost-block">
                <b>{rid}</b>
                <table className="mini">
                  <tbody>
                    {rows.map((r, i) => (
                      <tr key={i} className={r.cost === null ? "bad" : ""}>
                        <td>{r.condition}</td><td className="r">{r.cost === null ? "unreachable" : fmtMs(r.cost)}</td><td className="muted">{r.via}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ))}
          </>
        )}
        {selected && (
          <>
            <h3>{selected.label}</h3>
            <p className="muted small">{selected.type}{selected.cost != null ? ` · cost ${fmtMs(selected.cost)}` : ""}</p>
            {reqText && <div className="req-quote">{reqText}</div>}
            {selected.type === "fact" && plan && (
              <>
                <h4>Cheapest plan {plan.ok ? `· ${fmtMs(plan.cost)}` : "· NOT REACHABLE"}</h4>
                <ol className="plan">
                  {plan.steps.map((s, i) => <li key={i}>{stepText(s)[0]} <span className="muted small">{fmtMs(s.est_ms)}</span></li>)}
                  {plan.steps.length === 0 && plan.ok && <li className="muted">Already true after reset</li>}
                </ol>
                {plan.warnings.map((w, i) => <div key={i} className="warn small">⚠ {w}</div>)}
              </>
            )}
          </>
        )}
      </aside>
    </div>
  );
}
