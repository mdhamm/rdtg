# RDTG — Requirements-Driven Test Generation: Implementation Plan

## 1. Goal

Systems engineers write requirements ("The software shall X when Y and Z [within T ms]").
The tool:

1. Parses requirements into a structured form (trigger, conditions, response, timing).
2. Links requirements to an **interface catalog** (connection points, ICD messages, actions) that says *how* to stimulate and observe the system under test (SUT).
3. Builds a **dependency graph** automatically — a requirement whose response sets `ENGINE_RUNNING` becomes a way to satisfy any other requirement that needs `ENGINE_RUNNING` as a precondition.
4. Weights graph edges by **time cost**, and picks the cheapest way to reach each test's setup state.
5. Generates positive and negative test cases using a user-selected strategy (all combinations, pairwise, MC/DC-style, N random, minimal).
6. Emits two artifacts per test: **Test Documentation** (human-readable procedure) and an **executable test**.

## 2. Core concepts (data model)

### 2.1 Interface catalog (the "how")
Stored as YAML in the project folder so it's diffable and reviewable.

```yaml
connection_points:
  - name: IGNITION_SW
    kind: discrete            # discrete | analog | enum
    direction: input          # input = we drive it, output = we observe it
    values: [OFF, ON]
    set_cost_ms: 20           # time to actuate
    settle_ms: 50             # time before SUT reliably sees it
  - name: ENGINE_RUNNING
    kind: discrete
    direction: output
    values: [false, true]

messages:                     # from SICDs — normalized regardless of source format
  - name: ENG_STATUS
    transport: mil1553        # mil1553 | ieee1394 | ethernet_udp | ethernet_tcp | ...
    address: { rt: 5, sa: 3 } # transport-specific, opaque to the core
    rate_hz: 50
    fields:
      - { name: rpm, type: uint16, scale: 0.25, units: rpm }
      - { name: running, type: bool, bit: 0 }

actions:                      # named multi-step procedures (macros)
  - name: start_engine
    steps:
      - set: { IGNITION_SW: ON }
      - wait_until: { ENGINE_RUNNING: true, timeout_ms: 5000 }
    produces: { ENGINE_RUNNING: true }
    cost_ms: 4000             # can be measured and fed back later
```

Key design point: the core never knows about 1553/1394/Ethernet details. Each transport is an
**adapter plugin** implementing `send(message, fields)`, `read(message) -> fields`, `set(cp, value)`,
`get(cp)`. Non-standard SICDs are handled by writing an importer (or hand-editing YAML) that maps
into the normalized `messages` schema above.

### 2.2 Requirements (the "what")
Use the **EARS** pattern family (Easy Approach to Requirements Syntax) as the structured grammar,
since it matches the "shall … when …" style systems engineers already write:

| Pattern | Template |
|---|---|
| Event-driven | When `<trigger>`, the software shall `<response>` [within `<T>`]. |
| State-driven | While `<state>`, the software shall `<response>`. |
| Unwanted | If `<condition>`, then the software shall `<response>`. |
| Compound | When `<trigger>` and `<cond>` [and …], the software shall … |
| Ubiquitous | The software shall `<response>`. |

Parsed form:

```yaml
id: SRS-101
text: "When IGNITION_SW is ON and FUEL_PUMP_OK is true, the software shall set ENGINE_RUNNING to true within 500 ms."
trigger:     { IGNITION_SW: ON }
conditions:  [ { FUEL_PUMP_OK: true } ]
response:    { ENGINE_RUNNING: true }
timing:      { max_ms: 500 }        # optional; min_ms also supported
```

- Signal names in requirement text are resolved against the catalog. Unknown names are flagged.
- **Timing linter**: when a requirement has an observable response but no timing bound, the editor
  warns "No timing specified — is this intentional?" The writer can mark it `timing: none` to
  acknowledge. Default test timeout comes from project config.
- Free-text that doesn't match a pattern is kept but flagged "unparsed"; the user fixes it in a
  structured form editor. (Optional later: LLM-assisted parsing, see §7.)

## 3. Dependency graph

**Nodes** = state facts (`ENGINE_RUNNING=true`, `IGNITION_SW=ON`).
**Edges** = ways to make a fact true:

| Edge source | Example | Weight |
|---|---|---|
| Direct drive of an input CP | `→ IGNITION_SW=ON` | `set_cost_ms + settle_ms` |
| Message injection | send `CMD_MODE{mode=2}` | per-message cost |
| Action | `start_engine → ENGINE_RUNNING=true` | `cost_ms` |
| Requirement (SUT behavior) | SRS-101: `{IGNITION_SW=ON, FUEL_PUMP_OK=true} → ENGINE_RUNNING=true` | timing bound (or default) |

Because a requirement needs *all* its conditions, this is an **AND/OR hypergraph**: a fact is
satisfied by the cheapest of its incoming edges (OR), and an edge costs its own weight plus the
cost of all its preconditions (AND). Solved with a Dijkstra-style fixpoint (Knuth's generalization
for AND/OR graphs), with cycle detection.

Uses of the graph:
- **Setup planning** — for each test, compute the cheapest ordered sequence of steps to reach the
  precondition state. A slow "real" path (start the engine, 4 s) loses to a fast one (force a
  discrete, 70 ms) when both exist; the user can mark edges as *disallowed* (e.g. "don't force
  this signal in qualification tests").
- **Conflict detection** — preconditions that require contradictory values of one signal.
- **Coverage / impact** — "which tests are affected if SRS-101 changes?"; unreachable facts
  (nothing in the catalog or any requirement can produce them) are reported.
- **Visualization** in the UI.

## 4. Test case generation

Per requirement, the generator builds a set of **condition variables** (trigger + each condition)
plus optional **timing variants**.

- **Positive case**: all conditions satisfied → expect response within `max_ms`.
- **Negative cases**: one or more conditions violated → expect response *not* to occur within the
  observation window (or expect the "unwanted" response).
- **Timing boundary cases** (if timing present): verify response appears before `max_ms`; for
  `min_ms`, verify it does *not* appear early.
- **Value boundary cases** for analog/enum signals with ranges (`> 100 rpm` → test 99, 100, 101).

User-configurable **strategy** (project default + per-requirement override):

| Strategy | Cases for n binary conditions |
|---|---|
| `all` | 2ⁿ (full combinatorial) |
| `mcdc` | n+1 — each condition shown to independently affect the outcome |
| `pairwise` | covering array of all value pairs |
| `one_negative` | 1 positive + n single-fault negatives |
| `random:N` | N sampled combinations (seeded, reproducible) |
| `positive_only` | 1 |

Optional cap `max_cases_per_requirement`. Cases are then **ordered to minimize total runtime**:
consecutive cases that share setup state reuse it instead of resetting (greedy nearest-neighbor
over setup-state distance using the graph weights). The estimated run time per test and per suite
is shown before generation.

## 5. Outputs

### 5.1 Test Documentation
Per test procedure (Markdown + HTML; DOCX export in a later phase):
- Test ID, title, traced requirement IDs (traceability matrix appendix)
- Objective, preconditions, required equipment / transports used
- Numbered steps: *Action* | *Expected result* | *Timing* | *Pass/Fail* column
- Estimated duration

### 5.2 Executable test
Generated **pytest** files that call a small harness API — readable enough that a test engineer
can review them:

```python
def test_SRS_101_pos(rig):
    """SRS-101 positive: ENGINE_RUNNING set within 500 ms."""
    rig.set("FUEL_PUMP_OK", True)          # setup (cheapest path, 70 ms)
    rig.set("IGNITION_SW", "ON")           # trigger
    rig.expect("ENGINE_RUNNING", True, within_ms=500)
```

`rig` dispatches to the transport adapters of a named **test target** (simulated, partially
simulated, or real hardware — see README "Test targets"). A separate executable model of the
requirements is used only for the *suite check* (fault injection to measure test effectiveness);
it is never a test target, because passing against the requirements themselves proves nothing. Templates (Jinja2) so the
output can later target other frameworks/languages.

## 6. Architecture & tech stack

```
rdtg/
  backend/  (Python 3.12+)
    rdtg/model/        pydantic models: catalog, requirement, test case
    rdtg/parse/        EARS parser + signal resolution + linter
    rdtg/graph/        AND/OR dependency graph, cost solver, setup planner
    rdtg/generate/     strategies (all, mcdc, pairwise, ...), case ordering
    rdtg/emit/         doc + pytest emitters (Jinja2 templates)
    rdtg/harness/      runtime `rig` API, transport adapters, targets, requirements model
    rdtg/api/          FastAPI REST endpoints
    rdtg/cli.py        `rdtg lint | graph | generate | run`
    tests/
  frontend/ (React + TypeScript + Vite)
    Requirements editor (text + structured form, live lint/parse)
    Interface catalog editor
    Dependency graph view (Cytoscape.js), edge weights, path highlighting
    Generation config + preview (case counts, est. duration)
    Output viewer / download
  examples/engine_controller/
    workspace/            sample RDTG workspace (requirements, catalog, rig adapters)
    software/             independent software under test + its ICD
```

Project data lives in a plain folder (`catalog.yaml`, `requirements/*.yaml`, `rdtg.yaml` config) so
it works with git and the CLI independently of the UI.

## 7. Phases

1. **Core model + parser** — pydantic models, YAML load/save, EARS parser, signal resolution,
   timing linter. Unit tests.
2. **Dependency graph** — build AND/OR graph, cost solver, setup planner, conflict/unreachable
   detection. Unit tests.
3. **Test generation** — strategies, timing/boundary variants, runtime-aware ordering.
4. **Emitters** — test documentation (MD/HTML) and pytest output.
5. **Harness + targets** — `rig` API, adapter interface, named test targets; generated tests pass
   against the example's independent software (Simulated Hardware target).
6. **CLI** — usable end-to-end without the UI.
7. **Web UI** — FastAPI + React: editors, graph view, generate & download.
8. *Later / optional*: DOCX export, SICD importers (Excel/CSV templates), real transport adapters
   (1553/1394 via vendor libs), measured-cost feedback (update edge weights from actual run
   times), LLM-assisted parsing of free-form requirements.

Each phase ends with passing tests and something runnable.

## 8. Open questions / assumptions

- **Stack**: Python backend + React web UI (assumed). A desktop-only or pure-CLI tool is also
  possible.
- **Requirement input**: structured EARS grammar with a form editor (assumed) vs. LLM parsing of
  arbitrary prose from day one.
- **Generated test language**: pytest (assumed). Your old rig may have used something else
  (C#, TestStand, LabVIEW, Python) — the emitter is template-based, so this is swappable.
- **Negative case semantics**: assumed "response must NOT occur within the observation window";
  observation window defaults to the requirement's `max_ms` × a configurable factor.
