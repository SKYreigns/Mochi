# Mochi / JARVIS Agent Reliability Report

Date: 2026-11-05

Scope: backend orchestration, persistence, approvals, Ollama/Qwen3 configuration, and the
desktop chat/tool UI. No frontend redesign, no paid API, no cloud fallback, no extra model.
Branch: `main` (baseline HEAD `9ede8e3`).

## A. Executive summary

| Area | Before | After | Evidence |
|---|---|---|---|
| Backend tests | 100 passed | **132 passed** in 68.9 s | `pytest -q` |
| Frontend tests | 52 passed | **60 passed** in 9.0 s | `pnpm test:run` |
| Typecheck / lint / build | green | **exit 0 / exit 0 / exit 0** | `pnpm typecheck`, `pnpm lint`, `pnpm build` |
| Tool history | ephemeral, lost on restart | persisted per conversation/task with status, evidence, approval and timestamps | SQLite + `/api/chat/conversations/{id}/executions` |
| Restart recovery | running tasks stuck forever | stale tasks/executions recovered, stale approvals cancelled with audit rows | startup recovery + tests |
| Failed tool calls | task could still report success | any unresolved failed execution fails the task | `_finalize_failure` path + tests |
| Planner (qwen3:4b) | 33–113 s, occasionally malformed JSON | **3.4–3.5 s** with valid JSON | real Ollama benchmark + live tasks |
| Two-tool live task | 121.5 s, 80 schemas, wrong summary | **64.0 s**, 3 schemas, grounded summary | task `tsk_d9e5088a...` |
| Approval gate | approve/reject UI verified | still blocks, executes exactly once on approve, never executes on reject | live Playwright runs |
| Audit trail | ambiguous `tool.executed` for denials | `tool.executed` only for real attempts; `tool.skipped`/`tool.rejected`/`tool.recovered` distinguish outcomes | audit rows + tests |

No paid API credits, cloud models, or model downloads were used. Simulated tests use scripted
providers and temporary databases; the benchmark and live task/UI runs used the real local
`qwen3:4b` through Ollama at `127.0.0.1:11434`.

## B. Scope and environment

- Backend: FastAPI, SQLAlchemy async, SQLite (`data/jarvis.db`), `127.0.0.1:8765`.
- Frontend: Vite/React/Tauri desktop shell at `127.0.0.1:1420`.
- Local model: Ollama `qwen3:4b`, temperature `0.2`, `keep_alive="10m"`, thinking left at
  the model default for agent turns (`think=null`).
- Permission mode during live verification: `assisted`.
- Verification artifacts (outside the repo):
  `%TEMP%\opencode\ollama-baseline.json`, `ollama-options.json`,
  `live-two-tool-*.json`, `ui-approval-approve.json`, `ui-approval-reject.json`,
  screenshots and backend logs.
- A backup of the pre-migration database was written to
  `%TEMP%\opencode\jarvis-<stamp>.db` before schema changes. The live database retains the
  original 11 conversations / 15 tasks plus verification history.

## C. Persistence and restart recovery

### Schema

`backend/app/database/models.py` extends `ToolExecution` with:

`conversation_id`, `status`, `verified`, `evidence`, `hint`, `approval_status`,
`started_at`, `completed_at` (in addition to the existing task/step/tool/arguments/ok/error
columns), and adds `Message.task_id`.

### Migration

The project has no migration framework. `backend/app/database/migrations.py` performs
idempotent column additions and backfills on every startup, after
`Base.metadata.create_all`:

- inspect existing columns, add only missing ones;
- backfill `conversation_id` from the owning task;
- backfill `status` from the legacy `ok` flag;
- backfill `started_at` from `created_at`;
- never drops or rewrites existing rows.

The live database logged exactly the expected additions:
`approval_status, completed_at, conversation_id, evidence, hint, started_at, status,
task_id, verified`.

### Lifecycle

`backend/app/orchestrator/agent.py` now:

1. creates an execution row before a tool runs with `status="running"`;
2. updates it to `pending_approval` while a dialog is open;
3. updates it on completion to `completed` / `failed` / `skipped` with `ok`, `verified`,
   `evidence`, `error`, `hint`, `approval_status`, `completed_at`;
4. persists unknown tools as failed executions instead of silently ignoring them;
5. marks every still-running execution as `cancelled`/`failed` when the task terminates.

`backend/app/services/task_service.py` recovers stale state on startup:
running tasks become `failed` with an explanatory error, their executions are closed, and
`backend/app/permissions/engine.py::mark_stale_approvals` cancels pending approvals and
writes `approval.cancelled` audit rows.

### API and UI

- `GET /api/chat/conversations/{id}/executions` returns the persisted execution history.
- Messages now include `task_id`; the full internal `result` payload is not exposed in the
  conversation message list.
- `apps/desktop/src/stores/chat.ts` rehydrates terminal executions into tool cards, seeds
  live events for executions that are still open, deduplicates by execution id, and
  reconciles persisted records with websocket updates.
- Approval badges are rendered on tool cards.

### Settings persistence

A partial `llm` settings patch previously persisted only the patched sub-object, which erased
the configured model after a restart (`{"base_url": ""}`). `SettingsService.set` now persists
the merged nested object, so partial patches cannot destroy unrelated provider settings.

## D. Approval lifecycle

All approval state changes are persisted and audited:

| Transition | Approval status | Audit event |
|---|---|---|
| Requested | `pending` | `approval.requested` |
| User approves/rejects | `approved` / `denied` | `approval.resolved` |
| Timeout | `expired` | `approval.expired` |
| Task cancelled | `cancelled` | `approval.cancelled` |
| Backend restart | `cancelled` | stale-approval audit row |

The tool itself is never invoked before a positive decision. A denied call is persisted as a
`skipped` execution with `approval_status="denied"`, and the task fails rather than pretending
the action happened. In `apps/desktop/src/stores/chat.ts`, `approval.resolved` now only
updates running cards belonging to the matching task, avoiding cross-task badge changes.

`backend/tests/test_approval_lifecycle.py` (6 tests) covers approve, deny, expiry, stale
approval recovery, audit rows, and the absence of execution on denial.

## E. Qwen3 / Ollama performance and reliability

### Provider changes

`backend/app/providers/ollama.py`:

- configurable `think`, `keep_alive`, `num_ctx`; an explicit `None` preserves the model
  default and never silently disables thinking;
- per-call `think` and `json_mode` overrides;
- `tool_name` is serialized on tool-result messages (required by Ollama to associate a result
  with its originating call; without it small models can repeat calls);
- timing metrics: wall time, TTFT, prompt/eval durations, tokens/second.

`backend/app/providers/manager.py` wires the runtime settings and includes the new options in
the cache signature.

`backend/app/orchestrator/planner.py` uses `think=False` plus Ollama JSON mode for planning
only. Planning is structured extraction, not open-ended reasoning; agent turns keep the
configured thinking behaviour.

### Reproducible benchmark

`backend/scripts/benchmark_ollama.py` uses only the local Ollama endpoint and the real
`qwen3:4b`; it records raw runs and medians.

Final run (`ollama-options.json`, 2 runs per configuration):

| Configuration | Cold start wall | Warm chat median (min–max) | TTFT median | tok/s | Two-tool selection median | Safe selection |
|---|---:|---:|---:|---:|---:|---:|
| `think=default` | 6 589.9 ms | 5 275.4 ms (4 946–5 605) | 84.4 ms | 35.6 | 15 349.1 ms | 2/2 |
| `think=off` | 8 330.1 ms | 4 764.1 ms (4 405–5 123) | 85.5 ms | 35.3 | 14 748.6 ms | 2/2 |

An earlier baseline run (`ollama-baseline.json`) measured 6 225.1 ms warm for thinking-on
(4 471–7 979 ms), 4 132.0 ms for thinking-off, and displayed the same run-to-run variance.
Thinking-off was faster in plain chat but not reliably faster at two-tool selection
(12 858–16 639 ms in the final run).

### Live planner comparison

| Task | Planner | Schemas sent | Plan quality | Outcome |
|---|---:|---:|---|---|
| Baseline `tsk_e1156f4120c8412e838f2ea92abae79f` | 48.6 s | 80 | fallback, no tools | 121.5 s; 2 tools ran; summary mislabeled RAM as disk |
| Optimized `tsk_d9e5088a22294bac9aad1296e080f6b5` | **3.43 s** | 3 | CPU, RAM steps | **64.0 s**; correct grounded summary |
| Thinking-off `tsk_b90140f5e9a447d2b4c445e31b3bf165` | 3.35 s | 3 | CPU, RAM steps | 80.7 s; leaked chain-of-thought into the visible answer |

Decision: planner uses no-thinking + JSON mode; agent turns remain `think=null` so thinking is
not silently removed. The thinking-off agent experiment was rejected because it exposed
internal reasoning to the user.

## F. Multi-tool orchestration

`backend/tests/test_multi_tool.py` (7 tests) verifies:

- planner tool-schema filtering (only relevant tools reach the model);
- result-to-call association, including two different tools in one turn;
- a failure in the second tool does not mask a successful first tool, but still fails the task;
- a crashing tool is isolated and its execution is persisted;
- repeated identical failures are bounded (stops after 3 repeats / 4 calls);
- the max-step bound terminates a runaway loop;
- cancellation and the emergency stop stop execution.

Failure semantics: a task may only complete when every executed action is successful or
explicitly verified. Any unresolved failed execution now produces
`Task not reported as complete`, even if other tools succeeded. Tool failures include a hint
and the final answer lists succeeded/failed/unverified counts.

Text-encoded tool calls: small local models sometimes print a tool call as JSON text instead
of calling natively. The orchestrator now recognizes an exact JSON object that names a
registered tool and converts it into a real `ToolCall`, gated by the normal permission and
approval flow. The raw JSON is no longer stored as the assistant answer or shown to the user.
Non-tool JSON answers are left untouched. Audit event: `tool.recovered`.

Audit event naming now distinguishes real attempts from denials and unknown tools:

- `tool.executed` — the tool actually ran (success or failure);
- `tool.skipped` — a pending call was denied/blocked before execution;
- `tool.rejected` — the model named an unknown tool; counted as a failure;
- `tool.recovered` — a text-encoded call was salvaged.

## G. Live end-to-end verification (real Ollama + real UI)

All runs below used the real backend, real SQLite database, real `qwen3:4b`, and a headless
Playwright browser at `127.0.0.1:1420`. No mock provider was involved.

### Approval — approve

`tsk_ba7556e8bfeb4cf8aeb7249d8506715a`, approval `apr_e85c508f8b3844719813870237feb6a1`.
The harmless command only creates and removes a marker file under `%TEMP%\opencode`.

- dialog visible, mentions permission and `system.run_powershell`;
- execution `pending_approval` before the decision; marker did not exist;
- after **Approve action**, marker existed, execution `completed / ok=true / verified=true /
  approval_status=approved`, task `completed`;
- audit: `approval.requested`, `approval.resolved`, `tool.executed`;
- `message_count=2` (user + final answer), no raw tool-call JSON;
- console errors: 0.

### Approval — reject

`tsk_3b6cd05bc337487e93a83e0ac934e21d`, approval `apr_75489a02dc0f4f60b0a9e03ad70de402`.

- dialog visible before any execution; marker never created;
- execution `skipped`, `approval_status=denied`, task `failed` with an honest error;
- audit: `approval.requested`, `approval.resolved`, `tool.skipped` — **no** `tool.executed`;
- `message_count=2`, final message explains that the command was not executed;
- console errors: 0.

The same approve/reject behavior was reproduced in earlier runs
(`tsk_f932cd8e...` / `tsk_8a482c9b...`), including the regression test
`backend/tests/test_approval_lifecycle.py`.

## H. Automated regression suite

Backend `pytest -q`: **132 passed** in 68.9 s.

| Test file | Tests | Focus |
|---|---:|---|
| `tests/test_api.py` | 12 | API contracts and settings validation |
| `tests/test_approval_lifecycle.py` | 6 | approve/deny/expire/stale approvals, audit |
| `tests/test_documents.py` | 9 | document tools |
| `tests/test_execution_history.py` | 11 | persistence, migration, API, recovery, unknown tools |
| `tests/test_filesystem.py` | 9 | sandbox and file operations |
| `tests/test_integration.py` | 6 | end-to-end backend flows |
| `tests/test_memory.py` | 8 | memory tools |
| `tests/test_multi_tool.py` | 7 | multi-tool, failure, loops, cancellation, stop |
| `tests/test_ollama_provider.py` | 7 | payload options, tool_name, JSON mode, metrics |
| `tests/test_orchestrator.py` | 17 | agent loop, salvage, grounding, failure semantics |
| `tests/test_permissions.py` | 7 | permission engine |
| `tests/test_registry.py` | 6 | tool schemas |
| `tests/test_scheduler.py` | 15 | scheduling |
| `tests/test_websocket.py` | 5 | event streaming |
| `tests/test_workflows.py` | 7 | workflow checkpoints and cancellation |

Frontend: **60 passed** in 3 files, `pnpm typecheck` exit 0, `pnpm lint` exit 0 (pre-existing
warnings only), `pnpm build` exit 0.

Commands:

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q

cd ..\apps\desktop
pnpm test:run
pnpm typecheck
pnpm lint
pnpm build
```

## I. Security and permission audit

Reviewed paths: `backend/app/core/security.py`, `permissions/engine.py`,
`tools/system/tools.py`, `tools/filesystem/tools.py`, `services/task_service.py`,
`providers/manager.py`, `database/migrations.py`.

Findings:

- **Consequential tools are approval-gated**: `memory.delete`,
  `lms.submit_assignment`, `desktop.close_application`, `files.delete`,
  `files.organize`, `system.run_powershell` all declare
  `requires_approval=True` and are re-evaluated by the engine at call time.
- `observe` mode is strictly read-only; write/destructive/admin/sensitive actions are denied
  before any tool side effect.
- Reversible `WRITE` tools remain allowed without a dialog in `assisted` mode, matching the
  existing policy; no approval requirement was removed or weakened.
- Filesystem writes go through `PathPolicy` (sandbox + sensitive-name/suffix blocking);
  PowerShell has an allowlist, blocked-pattern checks, and a separate admin path.
- Provider API keys remain keyring-only; no key is written to logs, SQLite, or git.
- Retries are bounded (`4` calls / max steps); the emergency stop is checked both in the agent
  loop and in the permission engine.
- Stale approvals are cancelled at startup; no silent mock/cloud fallback exists.
- Audit rows persist approval outcomes and execution outcomes; secrets are redacted with
  `redact_value` / `redact`.

## J. Remaining limitations and risks

These are real, observed limitations — not fixed by this change set:

1. **Planner over-selection**: the optimized live plan still included
   `documents.summarize_document` as an unused third step. It did not run, but plan display and
   schema selection can be noisy. Mitigation: planner schema filtering and grounding; future
   work can score/trim steps before display.
2. **Zero-tool / no-op answers are not salvaged**: the text-call recovery only handles an exact
   JSON object naming a registered tool. A model that answers "I created the file" without any
   tool call can still produce a plausible but unexecuted claim. The task completion check
   catches failed executions, not fabricated prose. Future work: require evidence for claims
   of state change.
3. **Emergency stop is in-memory**: restarting the backend clears the stop flag. Approvals and
   executions are durable, but the kill switch itself is not persisted.
4. **Multi-task event scoping is partial**: tool cards carry `task_id` and approval badges are
   task-scoped, but some global websocket event streams (health, settings) remain
   session-wide. Concurrent tasks in multiple windows can still interleave non-tool events.
5. **Single-process SQLite**: WAL and busy timeout help, but concurrent writers (multiple
   backend processes or heavy parallel tasks) remain a scaling limit.
6. **Local-model variance**: Qwen3 warm latency varied 4.4–8.0 s and two-tool selection
   12.9–16.6 s across runs. Planner latency is now consistently ~3.4 s, but the agent turns
   still depend on machine load and model residency.
7. **Approval timeout / long waits**: expired approvals fail the task by design; the user must
   re-run. There is no resume-from-approval checkpoint beyond the existing workflow
   checkpoints.
8. **Frontend bundle size**: production build succeeds with a pre-existing >500 kB chunk
   warning (`index-*.js` ~1.1 MB). Not addressed here to avoid scope drift.
9. **No cloud/provider fallback**: intentional. If Ollama is unavailable, tasks fail with a
   configuration error rather than silently degrading.

## K. Commit and push status

- Branch `main`, baseline HEAD `9ede8e3` (`feat: Mochi pastel AI desktop companion`).
- All reliability changes are committed together with this report after the final green test
  run. See `git log -1` for the exact hash.
- Push is performed only if the remote state is a safe fast-forward; no force-push and no
  history rewrite.
