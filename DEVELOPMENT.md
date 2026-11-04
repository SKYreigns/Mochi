# JARVIS — Developer Log

This file records what was actually built, what was verified, and what remains.
The current phase is **Mochi desktop frontend and Tauri 2 shell complete**, on top
of the stabilized JARVIS backend. See `docs/MOCHI_IMPLEMENTATION_REPORT.md` for
the frontend report, screenshots and honest limitations.

Last updated: 2026-11-04

## Phase 0 — Environment & architecture

**Completed**
- Inspected the repository and Windows toolchain: Windows 11, Node 24.22, pnpm
  12.4, Python 3.15.2 (+3.13), MSVC Build Tools 2026, WebView2.
- Installed the stable Rust MSVC toolchain via rustup.
- Created the monorepo layout and the backend virtual environment; installed
  backend dependencies and Playwright Chromium.
- Wrote `docs/ARCHITECTURE.md` covering architecture, compatibility, directory
  layout, dependency inventory, roadmap and risk register.

**Verified**
- Python imports, dependency installation and Chromium launch work.

**Limitations**
- No LLM credentials or local model were provided. Ollama and Tesseract were
  absent; JARVIS uses graceful degradation and a deterministic `mock` provider
  for offline tests.

## Phase 1 — Application foundation

**Completed (backend scope)**
- FastAPI application factory with lifespan startup/shutdown.
- Asynchronous SQLite via SQLAlchemy + aiosqlite, schema creation and local user
  seed.
- Application settings persisted in SQLite with environment defaults, safe
  public snapshot, allowed-directory validation and runtime updates.
- In-process event bus with replay history and a WebSocket event stream.
- Health, tool catalog, settings, provider and model endpoints.

**Verified**
- Backend starts from current source on `127.0.0.1:8765`; database initializes;
  health, tools and settings return HTTP 200.
- Real WebSocket connection receives `hello`, history and live task events.
- Tests: `test_api.py`, `test_websocket.py`.

**Not started at that time**
- Tauri 2 + React/TypeScript/Vite/Tailwind/Zustand desktop shell. This was
  completed later in Phase 10–11.

## Phase 2 — AI brain

**Completed**
- Provider abstraction with OpenAI-compatible SSE, Ollama NDJSON, and explicit
  scripted/mock providers.
- Planner with validation and safe fallback when planning fails.
- Agent orchestration loop: plan, tool calls, retries, approval waits,
  cancellation, emergency stop, evidence collection, maximum steps and
  timeouts, false-success prevention.
- TaskService, conversation/message persistence and task lifecycle events.

**Verified**
- Scripted provider tests cover success, sequential calls, planning failure,
  provider failure, tool failure, retry limits, invalid arguments,
  approval/denial, cancellation, emergency stop, verification evidence and
  false-success prevention (`test_orchestrator.py`).
- Integration scenarios 1–4 exercise the loop with real tools.

**Limitations / not verified**
- Real model reasoning has not been exercised because no Ollama model or API
  key is available. The `mock` provider never reasons. Real planning quality,
  prompt-injection resistance and latency remain unverified.
- Exact model names are deliberately not hardcoded; the user configures them.

## Phase 3 — Browser automation

**Completed**
- Persistent Playwright Chromium profile with one context and multiple tabs.
- 17 browser tools: open, close, list, URL/title, snapshot, screenshot, click,
  type, fill, select, scroll, key press, extract text, find element, download,
  upload.
- URL scheme validation and configured blocked-site enforcement.
- Download capture and browser event publishing.

**Verified**
- Browser state/tabs endpoints return correct idle responses.
- A real headless Chromium instance navigated a controlled local HTTP site and
  extracted links/content in the integration LMS scenario.
- Browser tools pass registry schema validation.
- No test touches a real institution or the user's personal browser profile.

**Limitations**
- Login/MFA/CAPTCHA flows require a human and are not automated.
- External-site compatibility beyond the controlled test page is not verified.

## Phase 4 — Windows desktop automation

**Completed**
- 14 desktop tools: window listing/focus, active window, application launch/close,
  UI Automation queries, mouse and keyboard input, screenshots, OCR hook and
  process listing, with PowerShell policy checks.

**Verified**
- Tools are registered, schema-valid and permission-classified.
- Path/security policy checks are covered indirectly by filesystem and
  permission tests.

**Not verified**
- Interactive UI automation cannot be meaningfully asserted in headless CI and
  was not executed against real applications. This remains a manual-test area.

## Phase 5 — Filesystem & documents

**Completed**
- Sandboxed filesystem tools: create, read, copy, move, rename, list, search,
  metadata, organize/preview, and recoverable delete through trash.
- Protected system roots and sensitive-file rules; non-empty directory deletion
  requires recursion; root deletion refused; overwrite creates a backup.
- Document tools: PDF extraction, PDF creation, DOCX creation/extraction, XLSX
  creation/extraction, conversion, bounded text extraction and output-path
  validation.

**Verified**
- `test_filesystem.py` covers create/read/copy/move/rename/list, overwrite
  backups, sandbox escapes, traversal, protected/sensitive files, recoverable
  deletion, recursive deletion and invalid paths.
- `test_documents.py` covers PDF/DOCX/XLSX creation and extraction, invalid and
  missing inputs, output sandboxing, large-text bounds and conversion.

**Limitations**
- OCR requires the optional Tesseract binary and is not exercised.
- Huge multi-hundred-MB office files were not stress-tested.

## Phase 6 — LMS & assignment workspace

**Completed**
- Config-driven LMS service with no hardcoded institution or URL.
- Login/CAPTCHA/MFA detection that pauses and asks for manual authentication.
- Course/assignment listing and assignment reading with configurable selectors
  and heuristic fallback.
- Local assignment workspace, attachment download, generated-document records.
- Submission tool is always approval-gated, attaches files without submitting,
  verifies a receipt and never claims success without one.

**Verified**
- Integration test performs an assignment lookup against a controlled local
  HTTP site with real Playwright browser tools and returns title, due date and
  attachment.
- The submission permission path is enforced by the permission engine tests;
  the actual destructive submission action is not executed in tests.

**Limitations**
- Real LMS layouts, authentication flows and submissions are untested by design
  (never use the real college LMS or personal files).
- No blind submission is attempted; a receipt is required for a verified result.

## Phase 7 — Memory, workflows & scheduler

**Completed**
- Curated long-term memory with secret rejection and FTS5 indexed search over
  memory and conversation history.
- Workflow CRUD and execution with instruction/tool/approval steps, sequential
  ordering, `on_error=continue`, approval checkpoints, cancellation and run
  history.
- Durable scheduler supporting once/interval/daily schedules, validation,
  next-run computation, due-job dispatch, duplicate-running protection, and
  graceful shutdown/restart.
- Workflow deletion cleans up runs and detaches scheduled jobs to respect
  foreign keys.

**Verified**
- `test_memory.py`: CRUD, search, FTS5, history search, secret rejection and
  persistence across `AppServices` restart.
- `test_workflows.py`: create/edit/delete, sequential steps, failure stop,
  failure recovery, approval checkpoints, cancellation and run/task status.
- `test_scheduler.py`: validation, next-run math, execution, once-disable,
  interval advance, disabled/future skip, duplicate prevention, persistence and
  graceful stop/restart.
- Integration scenarios 3 and 4 cover recovery and cancellation.

**Limitations**
- Scheduler is in-process and assumes a single application instance; it is not a
  distributed job queue.
- Missed runs during downtime are advanced to the next future occurrence rather
  than replayed individually.

## Phase 8 — Voice

**Completed**
- Voice status endpoint, transcription endpoint with size/type validation,
  optional TTS endpoint and graceful degradation.
- Settings for push-to-talk, speak responses and wake word.

**Verified**
- Voice status API returns the honest capability snapshot.
- Test coverage is limited to status; it asserts no optional dependency is
  falsely reported as installed.

**Limitations**
- `faster-whisper`, `pyttsx3` and Tesseract are absent. Real STT/TTS was not
  exercised. The UI is expected to fall back to browser speech synthesis.

## Phase 9 — Security, tests, packaging & docs

**Completed**
- Permission engine enforced server-side for observe/assisted/autonomous modes,
  independent of the LLM.
- Approval waiters with timeout/denial/cancellation handling; emergency stop.
- Path sandbox, protected roots, sensitive-file blocking, secret detection and
  redaction in logs, memory and audit data.
- PowerShell policy checks and untrusted external content handling.
- 97-test backend suite across registry, permissions, orchestrator, filesystem,
  documents, memory, API, WebSocket, workflows, scheduler and integration.
- `docs/BACKEND_TESTING_REPORT.md` with totals, coverage, defects and limits.

**Verified**
- Full suite: 97 passed, 0 failed, 0 skipped.
- Repeated under Python development mode with `ResourceWarning` and
  `PytestUnraisableExceptionWarning` promoted to errors: 97 passed.
- Current-source Uvicorn server started, scheduler started, all route groups
  returned 200, WebSocket hello/live events verified.

**Not done / limitations**
- Packaging, installers and code signing are deferred until the frontend and
  Tauri shell exist.
- There is no network authentication because the backend is a local-only
  desktop service bound to `127.0.0.1`; do not expose it to untrusted networks.
- Real-model behavior, interactive desktop automation, external LMS layouts,
  OCR, STT/TTS and media downloads remain unverified without the required
  external dependencies/targets.

## Phase 10 — Mochi desktop frontend

**Completed**
- Rebranded the desktop application as **Mochi — Your little AI companion** with
  a Dreamy Pastel / Cozy Fantasy / soft-glass design system built on Tailwind 4
  theme tokens.
- Bundled Nunito and Nunito Sans locally; light theme by default with an optional
  dark theme, four accents and a reduced-motion preference.
- Original seven-state mascot (idle, listening, thinking, working, success,
  error, sleeping) rendered as inline SVG/CSS with Framer Motion.
- Implemented every requested screen: Home, Chat, Live Activity, My Tasks,
  Computer, Files & Documents, Workflows, Memory, Schedule and Settings.
- Real Chat streaming through `POST /api/chat` plus WebSocket `llm.delta`,
  `tool.*`, `task.plan`, `task.status` and `task.*` events; Markdown, GFM tables,
  highlighted code, attachments, stop/retry and honest failure states.
- Real approval dialog, emergency stop/resume, task filters and execution
  history, workflow CRUD/runs, memory CRUD/search/pin, schedule CRUD/run/pause,
  files browsing/search/preview/download, settings and provider configuration.
- Added one read-only backend route, `GET /api/system/windows`, exposing the
  existing desktop window tools to the Computer screen. No existing backend
  behaviour was changed.
- Typed API client, Zod validation for every form, Zustand stores for app/chat/
  live/toast state, idempotent WebSocket reconnect and honest unavailable states.

**Verified**
- `pnpm typecheck`: passed with TypeScript strict mode.
- `pnpm test:run`: **52 passed / 52** across helper, schema and store tests.
- `pnpm build`: production bundle generated.
- Headless Chromium smoke test visited every route and all seven Settings
  sections with no console or page errors; the primary creation dialogs opened.
- Live UI interactions created/searched/deleted a memory, created/deleted a
  schedule, and exercised emergency stop/resume through the real API.
- Screenshots captured for every screen in `docs/screenshots/`.

## Phase 11 — Tauri 2 shell and packaging

**Completed**
- Tauri 2 Rust shell with a native Windows window, standard minimize/maximize/
  close controls, generated Windows/macOS/iOS/Android icon set, and NSIS-only
  per-user bundle configuration.
- Narrow capability (`core:default`) with no shell, filesystem or unrestricted
  HTTP plugin access for the frontend.
- CSP limited to bundled assets, the loopback backend (`http/ws://127.0.0.1:8765`)
  and the Vite dev origin; `HashRouter` for custom-protocol safety.
- Dependency-free backend health probe (`GET /api/health` must return HTTP 200);
  the shell reuses an already running backend and only terminates the child it
  started. Logs go to the Tauri app log directory.
- Backend discovery through `MOCHI_BACKEND_DIR`, the resource directory, and
  bounded executable/manifest ancestor search.

**Verified**
- `cargo check`: passed.
- `pnpm tauri build --debug --no-bundle`: passed and produced
  `src-tauri/target/debug/mochi.exe` (12.4 MB).
- Launched the compiled `mochi.exe`: the native process stayed running and the
  pre-existing backend remained healthy (status 200), confirming the shell
  reuses an already running backend instead of spawning a duplicate.

**Not done / limitations**
- A release NSIS installer and code signing were not produced in this pass. The
  installer also does not embed a Python runtime; a fully self-contained package
  still needs a frozen backend executable in the Tauri resources (documented in
  `apps/desktop/README.md`).
- Real-model reasoning, external LMS layouts, MFA/CAPTCHA, OCR, STT/TTS and
  interactive desktop automation remain unverified without their external
  dependencies and human-driven targets.

## Current test totals

| Area | Tests |
|---|---|
| Registry and tool schemas | 6 |
| Permissions | 7 |
| Orchestrator | 13 |
| Filesystem | 9 |
| Documents | 9 |
| Memory | 8 |
| HTTP API | 12 |
| WebSocket | 5 |
| Workflows | 7 |
| Scheduler | 15 |
| End-to-end integration | 6 |
| **Backend total** | **97 passing, 0 failing, 0 skipped** |
| Frontend helpers / schemas / stores | **52 passing, 0 failing** |

## Recommended next phase

Install or configure a real model (Ollama or a provider key) and run end-to-end
chat, workflow and schedule executions against it. Then exercise interactive
desktop automation, browser flows against non-controlled sites and the real LMS
manually, with approval prompts enabled. After that, produce a signed release
installer, verify upgrade/uninstall behaviour, and perform accessibility and
reduced-motion audits on the packaged application.
