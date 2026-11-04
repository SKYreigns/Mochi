# Mochi — implementation report

**Product:** Mochi — *Your little AI companion.*
**Stack:** Tauri 2 + React 19 + TypeScript (strict) + Vite 8 + Tailwind CSS 4 +
Zustand + Framer Motion + Zod + React Markdown.
**Backend:** existing JARVIS FastAPI service at `http://127.0.0.1:8765` (not
replaced, not mocked).
**Date:** 2026-11-04

---

## 1. What was built

### Design system

- Dreamy pastel palette exposed as CSS variables and Tailwind `@theme` tokens:
  Cloud White `#F7F2FF`, Lavender `#E8DEFF`, Soft Aqua `#C9F1F1`, Blush Pink
  `#FFDCE9`, Vanilla Cream `#FFF1D9`, Lilac `#B9A1E8`, Seafoam `#8CCFCF`, Deep
  Ink `#554C69`.
- Nunito and Nunito Sans bundled locally through `@fontsource-variable/*`.
- Light theme by default, optional dark theme, four accents (lilac, seafoam,
  blush, honey), and a motion preference (`full`, `reduced`, `system`).
- Reusable UI primitives: buttons, cards, badges, fields, inputs, selects,
  switches, tabs, modals, skeletons, progress, empty states and toasts.
- Accessible focus rings, semantic landmarks, keyboard-dismissible modals,
  `aria-pressed` on selection controls, and `prefers-reduced-motion` awareness.

### Mascot

`src/components/Mascot.tsx` is an original inline SVG/CSS character with seven
real states: **idle, listening, thinking, working, success, error, sleeping**.
It uses different eye/mouth/brow geometry and Framer Motion transitions. No
emoji and no third-party mascot artwork are used. The same mark is exported as
`public/mochi-mark.svg` and drives the Windows icon set.

### Application shell

- Native Windows window through Tauri 2 with standard minimize/maximize/close.
- `HashRouter` so routing works inside the custom-protocol window.
- Persistent sidebar, backend status pill, permission/approval badge, theme and
  accent controls, compact responsive layout.
- Global approval dialog, toast host and realtime WebSocket lifecycle.
- Secure backend lifecycle: reuse an already healthy backend, otherwise spawn
  the local venv Python, wait for `/api/health`, and terminate only the child
  process Mochi started. Capabilities grant only `core:default`; no shell,
  filesystem or arbitrary HTTP plugin access is exposed to the frontend.

---

## 2. Implemented screens

| Screen | Route | What it really does |
|---|---|---|
| **Home** | `#/` | Time-aware greeting, real command composer, six quick actions, recent conversations, tasks, workflows and pending approvals, live mascot. |
| **Chat** | `#/chat`, `#/chat/:id` | Sends `POST /api/chat`, adopts the returned task, streams `llm.delta` / `tool.*` / `task.*` WebSocket events, renders Markdown, GFM tables and highlighted code, shows plan, real tool cards, stop, retry, error + hint, attachments and conversation history. |
| **Live Activity** | `#/activity` | Real task timeline, plan steps, tool evidence, browser URL/tabs, desktop and browser screenshots, and `POST /api/tasks/stop` emergency stop. |
| **My Tasks** | `#/tasks` | Filters by all/running/waiting/queued/completed/failed/cancelled, opens task details with execution history, cancels through `POST /api/tasks/{id}/cancel`. |
| **Computer** | `#/computer` | Real Windows metrics, disks, battery, uptime, top processes, foreground window and visible window list (`GET /api/system/windows`), managed browser state, screen capture. |
| **Files & Documents** | `#/files` | Sandbox-respecting roots, directory browsing, search, metadata, text preview, generated-document downloads and uploads. |
| **Workflows** | `#/workflows` | Real CRUD, duplicate, enable/pause, run, delete, step editor, and execution history from `/api/workflows/{id}/runs`. |
| **Memory** | `#/memory` | Real CRUD/search/pin/filter against `/api/memory`; type/tag editing with honest empty states. |
| **Schedule** | `#/schedule` | Real once/interval/daily CRUD, pause/resume, run-now, delete, validation and next-run display. |
| **Settings** | `#/settings?section=…` | Appearance, AI Models, Hardware, Permissions, Voice, Browser and General; every control saves through the real settings API or local preferences. |
| **Approval modal** | global | Shows exact action, reason, target and consequences; Approve/Reject call `POST /api/approvals/{id}/decide`; dismiss is never approval. |
| **Toasts** | global | Real success/error/warning feedback, capped stack, auto-dismiss. |

---

## 3. Backend integrations

The typed client in `src/lib/api.ts` uses these live endpoints:

- System: `/api/health`, `/api/events/history`, `/api/tools`.
- Settings: `/api/settings`, `/api/settings/provider`, `/api/settings/models`,
  `/api/settings/api-key`.
- Chat: `/api/chat`, `/api/chat/conversations`,
  `/api/chat/conversations/{id}/messages`, `/api/chat/tasks/{id}`.
- Tasks: `/api/tasks`, `/api/tasks/{id}`, `/api/tasks/{id}/cancel`,
  `/api/tasks/stop`, `/api/tasks/resume`.
- Approvals: `/api/approvals`, `/api/approvals/{id}/decide`.
- Files: `/api/files/roots`, `/list`, `/read`, `/metadata`, `/search`,
  `/api/files/organize/preview`, `/upload`, `/download`.
- Memory: `/api/memory`, `/api/memory/{id}`, `/api/memory/history/search`.
- Workflows: `/api/workflows`, `/api/workflows/{id}`, `/run`, `/runs`.
- Schedules: `/api/schedules`, `/api/schedules/{id}`, `/run`.
- Live/system: `/api/live/state`, `/api/live/screenshot`,
  `/api/system/metrics`, `/api/system/processes`, `/api/system/os`,
  `/api/system/applications`, `/api/system/windows`.
- Browser: `/api/browser/state`, `/open`, `/close`, `/screenshot`.
- Voice: `/api/voice/status`, `/api/voice/transcribe`, `/api/voice/speak`.
- Assignments: `/api/assignments`, `/api/assignments/{id}`,
  `/api/assignments/{id}/draft`.
- Realtime: `ws://127.0.0.1:8765/ws/events`.

### One small backend addition

`GET /api/system/windows` was added to `backend/app/api/routes/system.py`. It is
read-only and returns the existing `desktop.get_active_window` and
`desktop.list_windows` tool output for the Computer screen. No existing route or
behaviour was changed.

---

## 4. Verification

### Automated

| Check | Result |
|---|---|
| Frontend TypeScript (`pnpm typecheck`) | Passed, no errors |
| Frontend unit/store tests (`pnpm test:run`) | **52 passed / 52** in 3 files |
| Frontend production build (`pnpm build`) | Passed, `dist/` generated |
| Rust shell (`cargo check`) | Passed |
| Tauri debug application build (`pnpm tauri build --debug --no-bundle`) | Passed; produced `src-tauri/target/debug/mochi.exe` (12.4 MB) |
| Native `mochi.exe` launch | Passed; process stayed alive and the healthy pre-existing backend was reused (HTTP 200) |
| Backend pytest suite | **97 passed / 97** (78.56 s) |

The Vitest suite covers formatting/status helpers, all Zod schemas (memory,
workflow, schedule, model settings), toast behaviour, theme/accent/motion
application, chat event ingestion (task adoption, streaming deltas, tool
lifecycle, plan, failures) and composer guards.

### Live UI smoke test

A headless Chromium session visited every route and all seven Settings
sections while capturing console/page errors:

```
home chat activity tasks computer files workflows memory schedule
settings-appearance settings-models settings-hardware settings-permissions
settings-voice settings-browser settings-general
```

All headings rendered and **no console errors or page errors** were recorded.
The New memory, New workflow and New schedule dialogs opened correctly.

### Real interaction test

Through the UI, against the live backend:

- Created a memory record, found it through search, deleted it — all reflected
  in the backend.
- Created a one-time schedule, saw it in the list, deleted it.
- Triggered **Emergency stop** and confirmed the stopped state, then resumed
  through `POST /api/tasks/resume` (health reports `emergency_stopped: false`).

---

## 5. Screenshots

All screenshots are real captures of the running Vite frontend against the live
backend, at 1440 px wide with 1.5× device scale. Demo memories/workflows were
created through the real API for illustrative content and removed afterwards.

| File | Content |
|---|---|
| `docs/screenshots/home.png` | Home, command composer, quick actions |
| `docs/screenshots/home-dark.png` | Home in the dark theme |
| `docs/screenshots/chat.png` | New chat with honest “no model” state |
| `docs/screenshots/chat-conversation.png` | Existing conversation and history |
| `docs/screenshots/live-activity.png` | Live timeline, screen preview, browser state |
| `docs/screenshots/tasks.png` | Task list with real failed task and filters |
| `docs/screenshots/computer.png` | Real metrics, foreground/visible windows, processes |
| `docs/screenshots/files.png` | Sandbox browser and document tools |
| `docs/screenshots/workflows.png` | Real workflow card and controls |
| `docs/screenshots/memory.png` | Memory cards, search, pin and CRUD |
| `docs/screenshots/schedule.png` | Real daily/one-off schedules |
| `docs/screenshots/settings-appearance.png` | Theme, accent, motion |
| `docs/screenshots/settings-models.png` | Provider/model configuration |
| `docs/screenshots/settings-hardware.png` | Real hardware metrics and GPU limitation |
| `docs/screenshots/settings-permissions.png` | Permission mode, folders, tool approvals |
| `docs/screenshots/settings-voice.png` | Real voice capability reporting |
| `docs/screenshots/settings-browser.png` | Managed browser, blocked sites |
| `docs/screenshots/settings-general.png` | Runtime, data locations, emergency controls |

---

## 6. Honest limitations

1. **No model is configured.** The backend reports provider `ollama` with an
   empty model and no key. Chat reasoning and LLM-driven planning fail with a
   real configuration error. The UI says so on Home, Chat and Settings.
2. **GPU/VRAM telemetry is unavailable.** No backend endpoint exists, so the
   Hardware screen shows an explicit unavailable card. No numbers are invented.
3. **Voice is only partially available.** `faster-whisper` and `pyttsx3` are not
   installed; the backend reports this and the UI reports it honestly. Browser
   speech synthesis is exposed as available by the backend.
4. **Browser automation depends on Playwright.** The managed browser is closed
   until a task or the user opens a page; missing engine errors are surfaced.
5. **Window telemetry depends on `pywin32`.** It is installed in the current
   environment and the endpoint returns real windows; otherwise the UI shows the
   backend's real error.
6. **Real-model quality, external LMS layouts, MFA/CAPTCHA, OCR and interactive
   desktop automation remain unverified** without the required external
   dependencies and human-driven targets.
7. **The NSIS installer was not produced in this pass, and the installer does
   not yet embed a Python runtime.** The Rust application binary was compiled
   successfully and the shell reuses/spawns a local backend when one is
   discoverable. A fully self-contained installer still needs a frozen backend
   executable added to the Tauri resources (documented in the app README).
   Without a reachable backend, the packaged UI shows an honest offline state.

---

## 7. Start and package

See `apps/desktop/README.md` for the full instructions. Short version:

```powershell
# backend
cd backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8765

# UI (browser)
cd apps\desktop
pnpm dev

# native shell
pnpm tauri:dev

# production installer
$env:PATH = "$env:USERPROFILE\.cargo\bin;$env:PATH"
pnpm tauri:build
```
