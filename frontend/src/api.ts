export type Value = boolean | number | string | null;

export interface Condition { signal: string; op: string; value: Value }
export interface Timing { min_ms: number | null; max_ms: number | null }
export interface Requirement {
  id: string; text: string; pattern: string | null;
  trigger: Condition | null; conditions: Condition[]; response: Condition[];
  timing: Timing | null; timing_ack: boolean; strategy: string | null;
  parsed: boolean; parse_error: string | null; resolve_errors: string[]; tags: string[];
}
export interface RawRequirement { id: string; text: string; timing_ack?: boolean; strategy?: string; tags?: string[] }
export interface Issue { level: "error" | "warning" | "info"; code: string; message: string; requirement_id: string | null }
export interface Signal {
  name: string; kind: string; direction: string; values: Value[] | null; min: number | null; max: number | null;
  units: string | null; source: string; message: string | null; transport: string | null; set_cost_ms: number;
}
export interface ConnectionPoint {
  name: string; kind: string; direction: string; values?: Value[] | null; min?: number | null; max?: number | null;
  resolution?: number; units?: string | null; initial?: Value; set_cost_ms: number; settle_ms: number; description: string;
}
export interface MessageField {
  name: string; kind: string; type?: string; values?: Value[] | null; min?: number | null; max?: number | null;
  units?: string | null; initial?: Value; description?: string;
}
export interface Message {
  name: string; transport: string; direction: string; address: Record<string, unknown>;
  rate_hz?: number | null; send_cost_ms: number; fields: MessageField[]; description: string;
}
export interface Action {
  name: string; steps: Record<string, unknown>[]; requires: Record<string, Value>;
  produces: Record<string, Value>; cost_ms: number; description: string;
}
export interface Constraint { id: string; never: string; description: string }
export interface Catalog { connection_points: ConnectionPoint[]; messages: Message[]; actions: Action[]; constraints: Constraint[] }
export type TargetKind = "simulated" | "partial" | "hardware";
export interface TargetConfig {
  name: string; kind: TargetKind; description: string;
  adapters: Record<string, Record<string, unknown>>; start: string | null; params: Record<string, unknown>;
}
export interface Config {
  name: string; default_timeout_ms: number; negative_window_factor: number; strategy: string;
  max_cases_per_requirement: number | null; random_seed: number; disallowed_providers: string[];
  targets: TargetConfig[]; default_target: string | null;
}
export const TARGET_KIND_LABEL: Record<TargetKind, string> = {
  simulated: "Simulated hardware", partial: "Partially simulated", hardware: "Hardware",
};
export interface ProjectData {
  root: string; config: Config; catalog: Catalog; signals: Record<string, Signal>;
  raw_requirements: RawRequirement[]; requirements: Requirement[]; issues: Issue[];
  strategies: string[]; fault_kinds: string[];
}
export interface Step {
  kind: string; phase: string; signal: string | null; op: string; value: Value; message: string | null;
  fields: Record<string, Value>; action: string | null; ms: number | null; est_ms: number; via: string | null; note: string;
}
export interface TestCase {
  id: string; requirement_id: string; title: string; kind: "positive" | "negative";
  assignment: Record<string, boolean>; steps: Step[]; est_ms: number; traces: string[]; warnings: string[]; notes: string[];
}
export interface Untestable { requirement_id: string; assignment: Record<string, boolean>; reason: string; note: string }
export interface GenResult { tests: TestCase[]; issues: Issue[]; est_ms: number; pytest: Record<string, string>; untestable: Untestable[] }
export interface RunResult { id: string; status: "passed" | "failed" | "inconclusive"; message: string; transcript: string[] }
export interface GraphNode { id: string; type: string; label: string; cost: number | null; reachable?: boolean; signal?: string }
export interface GraphEdge { source: string; target: string; kind: string; weight: number }
export interface GraphData {
  nodes: GraphNode[]; edges: GraphEdge[];
  setup_costs: Record<string, { condition: string; cost: number | null; via: string | null }[]>;
}
export interface PlanResult { ok: boolean; cost: number; warnings: string[]; steps: Step[]; used: string[] }
export interface MutateResult {
  kinds: string[]; rows: Record<string, Record<string, string>>; counts: Record<string, number>; n_tests: number;
}
export interface WorkspaceInfo { open: boolean; path: string | null; name: string | null }
export interface RecentWorkspace { path: string; name: string; opened: string | null; exists: boolean }
export interface DirListing {
  path: string; parent: string | null; is_workspace: boolean; home: string; roots: string[];
  dirs: { name: string; path: string; is_workspace: boolean }[];
}
export interface GenParams { strategy?: string | null; max_cases?: number | null; requirement_ids?: string[] | null }

async function call<T>(method: string, url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch { /* not json */ }
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  workspace: () => call<WorkspaceInfo>("GET", "/api/workspace"),
  recent: () => call<RecentWorkspace[]>("GET", "/api/workspaces/recent"),
  forget: (path: string) => call<RecentWorkspace[]>("DELETE", `/api/workspaces/recent?path=${encodeURIComponent(path)}`),
  openWorkspace: (path: string) => call<WorkspaceInfo>("POST", "/api/workspaces/open", { path }),
  createWorkspace: (path: string, name: string) => call<WorkspaceInfo>("POST", "/api/workspaces/create", { path, name }),
  closeWorkspace: () => call<WorkspaceInfo>("POST", "/api/workspaces/close"),
  listDir: (path?: string) => call<DirListing>("GET", `/api/fs/list${path ? `?path=${encodeURIComponent(path)}` : ""}`),
  project: () => call<ProjectData>("GET", "/api/project"),
  saveRequirements: (raw: RawRequirement[]) => call<ProjectData>("PUT", "/api/requirements", raw),
  saveCatalog: (c: Catalog) => call<ProjectData>("PUT", "/api/catalog", c),
  saveConfig: (c: Config) => call<ProjectData>("PUT", "/api/config", c),
  parse: (id: string, text: string) =>
    call<{ requirement: Requirement; issues: Issue[] }>("POST", "/api/parse", { id, text }),
  graph: () => call<GraphData>("GET", "/api/graph"),
  plan: (c: Condition) => call<PlanResult>("POST", "/api/plan", c),
  generate: (p: GenParams) => call<GenResult>("POST", "/api/generate", p),
  run: (p: GenParams & { target?: string | null; params?: Record<string, unknown> }) =>
    call<{ results: RunResult[]; target: string }>("POST", "/api/run", p),
  checkTarget: (target: string | null, params: Record<string, unknown>) =>
    call<{ target: string; available: boolean; message: string }>("POST", "/api/targets/check", { target, params }),
  mutate: (p: GenParams) => call<MutateResult>("POST", "/api/mutate", p),
  exportFiles: (p: GenParams & { out_dir: string }) => call<{ files: string[] }>("POST", "/api/export", p),
};

export function fmtValue(v: Value | undefined): string {
  if (v === true) return "true";
  if (v === false) return "false";
  if (v === null || v === undefined) return "—";
  return String(v);
}

const SYM: Record<string, string> = { "==": "=", "!=": "≠", "<": "<", "<=": "≤", ">": ">", ">=": "≥" };

export function fmtCond(c: { signal: string | null; op: string; value: Value }): string {
  return `${c.signal} ${SYM[c.op] ?? c.op} ${fmtValue(c.value)}`;
}

export function fmtMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  if (ms >= 60000) return `${(ms / 60000).toFixed(1)} min`;
  if (ms >= 1000) return `${(ms / 1000).toFixed(2)} s`;
  return `${Math.round(ms * 10) / 10} ms`;
}

export function parseScalar(s: string): Value {
  const t = s.trim();
  if (t === "") return null;
  if (t.toLowerCase() === "true") return true;
  if (t.toLowerCase() === "false") return false;
  if (!isNaN(Number(t))) return Number(t);
  return t;
}

export function stepText(s: Step): [string, string] {
  switch (s.kind) {
    case "set":
      return [`Set ${s.signal} to ${fmtValue(s.value)}`, "—"];
    case "send":
      return [`Send ${s.message} with ${Object.entries(s.fields).map(([k, v]) => `${k} = ${fmtValue(v)}`).join(", ")}`, "—"];
    case "action":
      return [`Perform procedure '${s.action}'`, "Procedure completes"];
    case "wait":
      return [`Wait ${fmtMs(s.ms)}`, "—"];
    case "wait_until":
      return [`Wait for ${fmtCond(s)}${s.note ? ` (${s.note})` : ""}`, `${fmtCond(s)} within ${fmtMs(s.ms)}`];
    case "check":
      return [`Verify ${fmtCond(s)}`, fmtCond(s)];
    case "expect":
      return [`Monitor ${s.signal}`, `${fmtCond(s)} within ${fmtMs(s.ms)} of stimulus`];
    case "expect_not":
      return [`Monitor ${s.signal}`, s.note.startsWith("must not respond earlier")
        ? `${fmtCond(s)} does NOT occur before ${fmtMs(s.ms)}`
        : `${fmtCond(s)} does NOT occur within ${fmtMs(s.ms)}`];
    default:
      return [s.kind, ""];
  }
}
