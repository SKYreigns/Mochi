# JARVIS — Architecture

> Personal autonomous AI desktop assistant for Windows 11.
> Local-first, permission-controlled, tool-using agent.

## 1. System overview

```
┌──────────────────────────────────────────────────────────────────┐
│ Tauri 2 desktop shell (WebView2)                                 │
│  React 19 + TypeScript strict + Vite + Tailwind + Zustand        │
│  ┌────────────┐ ┌──────────┐ ┌──────────┐ ┌───────────────────┐  │
│  │ Chat       │ │ Live     │ │ Tasks /  │ │ Files / Memory /  │  │
│  │ + voice    │ │ Computer │ │ Approval │ │ Workflows / etc.  │  │
│  └─────┬──────┘ └────┬─────┘ └────┬─────┘ └────────┬──────────┘  │
└────────┼─────────────┼────────────┼────────────────┼─────────────┘
         │ HTTP + WebSocket (127.0.0.1:8765, events + streaming)  │
┌────────┼─────────────┼────────────┼────────────────┼─────────────┐
│ FastAPI backend                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ AI Orchestrator (agent loop, state, verification)        │   │
│  │  Provider Manager → OpenAI-compatible / Ollama / Mock     │   │
│  │  Task Planner · Execution Manager · Recovery              │   │
│  └───────┬───────────────────────────────┬──────────────────┘   │
│          │ validated tool calls           │ approvals            │
│  ┌───────▼──────────┐          ┌─────────▼───────────────────┐   │
│  │ Tool Registry    │          │ Permission & Approval Engine│   │
│  │ browser/desktop/ │          │ observe/assisted/autonomous │   │
│  │ files/documents/ │          │ path + command policy       │   │
│  │ system/LMS/media │          └─────────┬───────────────────┘   │
│  └───────┬──────────┘                    │ audit                  │
│  ┌───────▼────────────────────────────────▼───────────────────┐  │
│  │ Memory Manager · Workflow Engine · Scheduler · Voice        │  │
│  └───────┬────────────────────────────────────────────────────┘  │
│  ┌───────▼────────────────────────────────────────────────────┐  │
│  │ SQLite (SQLAlchemy async) + FTS5 · logs · screenshots      │  │
│  └────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

The **AI Orchestrator** is the only component that talks to an LLM. It never
touches the OS directly: every action is a registered tool with a Pydantic
schema, a permission class, a timeout, and an audit record. The backend enforces
permissions independently of the model.

## 2. Technology compatibility assessment (verified on this machine)

| Component | Version found | Decision |
|---|---|---|
| Windows | 11 Home 10.0.26300 | Supported |
| Node.js | 24.22.1 | Supported |
| pnpm | 12.4.1 | Supported |
| Python | 3.15.2 (also 3.13 available) | Supported; all required wheels installed on cp315 |
| Rust / cargo | absent → installed via rustup (stable, MSVC) | Tauri build supported |
| MSVC Build Tools | VS 18 BuildTools with link.exe | Rust MSVC toolchain supported |
| WebView2 | installed | Tauri runtime supported |
| Playwright Chromium | installed by setup script | Browser engine ready |
| Tesseract OCR | **not installed** | OCR tool degrades with a clear, actionable error; configurable `JARVIS_TESSERACT_PATH` |
| Ollama | **not installed** | Ollama provider available but requires local install; OpenAI-compatible provider is the alternative |
| Faster-Whisper | optional (`requirements-optional.txt`) | Voice STT activates when installed; UI uses Web Speech for TTS |

Python 3.15 note: the pinned dependency set was installed and imported
successfully from wheels; no build-from-source fallback is required.

### Documented trade-offs

1. **Local model default.** JARVIS ships with the Ollama provider selected and
   no model name hardcoded. If Ollama is absent, the API returns a configuration
   error that points to Settings. The `mock` provider exists for offline UI and
   automated tests and is explicitly labelled as non-reasoning.
2. **Tesseract** is an external binary; OCR returns an actionable error if it is
   not installed rather than silently failing.
3. **PDF generation** renders HTML through headless Chromium (high fidelity,
   tables/headings/images), falling back to PyMuPDF text layout if a browser is
   unavailable.
4. **Voice STT** needs the optional `faster-whisper` package and a model
   download; the integration is present and reports its status honestly.
5. **LMS DOMs differ per institution.** The LMS engine is selector-configurable
   and heuristic by default; it never hardcodes an institution, never bypasses
   login/MFA/CAPTCHA, and always requires explicit approval before submission.

## 3. Directory structure

```
Jarvis/
├── apps/desktop/                 Tauri 2 + React + TS frontend
│   ├── src/                      UI (pages, components, store, api, types)
│   └── src-tauri/                Rust shell, capabilities, icons, config
├── backend/
│   ├── app/
│   │   ├── api/routes/           REST + WebSocket routes
│   │   ├── core/                 config, events, logging, security, container
│   │   ├── database/             ORM models, async engine, FTS5 bootstrap
│   │   ├── providers/            OpenAI-compatible, Ollama, mock
│   │   ├── orchestrator/         agent loop, planning prompt, task state
│   │   ├── tools/                registry + browser/desktop/files/docs/system/LMS/media
│   │   ├── memory/               long/short-term memory + FTS search
│   │   ├── permissions/          modes, approval futures, path policy
│   │   ├── workflows/            reusable workflow engine
│   │   ├── scheduler/            durable recurring jobs
│   │   ├── services/             settings, voice, screenshots, tasks
│   │   └── main.py               app factory
│   ├── tests/                    pytest suite (isolated tmp dirs, fake LLM)
│   ├── requirements.txt
│   └── requirements-optional.txt
├── data/                         runtime DB, browser profile, screenshots, trash
├── logs/                         app + audit logs
├── scripts/                      setup / dev / build / self-check
├── docs/                         architecture, security, troubleshooting
├── .env.example
├── README.md
└── DEVELOPMENT.md
```

## 4. Implementation roadmap

| Phase | Deliverable | Status gate |
|---|---|---|
| 0 | Environment audit, architecture, compatibility, roadmap | this document + self-check script |
| 1 | Tauri + React shell, FastAPI, WebSocket, SQLite, settings | app launches, streams events |
| 2 | Provider abstraction, orchestrator loop, tool calling, streaming | fake-LLM test executes a registered tool |
| 3 | Playwright persistent browser + 18 browser tools | navigation, forms, screenshots, downloads verified |
| 4 | Windows desktop automation, screenshots, OCR hook | window list, focus, typing, screenshot |
| 5 | Filesystem + document engines, safe previews/backups | isolated-file tests pass |
| 6 | LMS configuration + assignment workspace + approvals | assignment extraction + generated PDF; submission approval-gated |
| 7 | Memory + workflows + scheduler | persisted memory/workflow/schedule tests |
| 8 | Voice (push-to-talk, STT, TTS) | optional deps; graceful status |
| 9 | Security hardening, tests, packaging, docs | full pytest suite + typecheck + build |

## 5. Dependency inventory

**Backend runtime:** fastapi, uvicorn, pydantic, pydantic-settings, SQLAlchemy
(async) + aiosqlite + greenlet, httpx, websockets, openai SDK (OpenAI-compatible),
keyring (Windows Credential Manager), psutil, mss, Pillow, pytesseract, pywinauto,
pywin32, playwright, PyMuPDF, python-docx, openpyxl, python-multipart.

**Optional:** faster-whisper (STT), yt-dlp (authorized media), pyttsx3 (TTS),
numpy.

**Frontend:** Tauri 2, React 19, TypeScript (strict), Vite, Tailwind CSS 4,
Lucide icons, Zustand.

**Dev:** pytest, pytest-asyncio, Vite build, `tsc --noEmit`, Cargo.

## 6. Technical risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Model hallucinates success | False completion claims | Only tool results enter state; final answer constrained to recorded evidence; verification fields on every tool result |
| Prompt injection from pages/PDFs | Unsafe actions | External content wrapped as untrusted data; permission engine enforced server-side; malicious-instruction tests |
| Destructive filesystem ops | Data loss | Allowed-root sandbox, protected-path blocklist, trash backups, bulk preview + approval |
| Infinite agent loops | Cost / runaway | Max steps, retries, timeouts, cancellation, emergency stop, no-progress detection |
| Browser session expiry | Broken workflows | Login detection pauses task and asks user; session reuse via dedicated profile |
| LMS layout drift | Extraction failures | Configurable selectors + heuristic fallback + LLM reasoning over extracted text; never blind-click submissions |
| Tauri/Rust build time | Slow first build | `scripts/setup.ps1` installs toolchain; browser dev mode available via `pnpm dev` |
| Secrets leakage | Credential exposure | Keys in keyring/env only; redaction in logs and settings API; secret-pattern rejection in memory |
| OCR absence | No screen text | Detect binary and report install instructions; optional UI Automation text fallback |

## 7. Runtime data flow (task lifecycle)

1. `POST /api/chat` persists the user message and creates a `Task` row.
2. Orchestrator builds context (system policy + relevant memories + history).
3. Provider streams assistant text / tool calls to `/ws/events`.
4. Each call is validated (`Pydantic`), classified, and authorized.
5. If approval is required, the task becomes `waiting_for_approval` and an
   `Approval` row is emitted; the wait is a cancellable asyncio future.
6. Tool executes with a timeout; result + verification are recorded in
   `tool_executions`, streamed to the UI, and fed back to the model.
7. Failures are returned to the model for re-planning within the retry budget.
8. Final answer is emitted only after the loop ends; the task stores evidence,
   completion time, and status. Emergency stop cancels every running task.
