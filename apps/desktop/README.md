# Mochi — desktop app

Mochi is a local-first Windows AI companion built with **Tauri 2**, **React 19**,
**TypeScript**, **Vite**, **Tailwind CSS 4**, **Zustand** and **Framer Motion**.
The UI talks only to the real JARVIS backend at `http://127.0.0.1:8765`; there are
no mock responses or simulated computer activity in the application.

## Prerequisites

- Node.js 20+ (verified with 24.22.1) and pnpm 10+ (verified with 12.4.1)
- Rust stable with the MSVC toolchain (verified with 1.99.1)
- The backend virtual environment at `../../backend/.venv`
- WebView2 (installed by default on current Windows 11)

## Development

From the repository root:

```powershell
# 1. Start the backend
cd backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8765 --log-level info

# 2. In another terminal, start the UI in a normal browser
cd apps\desktop
pnpm install
pnpm dev          # http://127.0.0.1:1420

# Or run the native Tauri window
pnpm tauri:dev
```

`pnpm tauri:dev` reuses an already healthy backend. If none is running, the Rust
shell searches for `backend/app/main.py` (via `MOCHI_BACKEND_DIR`, the bundled
resource directory, or the executable/Cargo manifest ancestors), starts
`backend/.venv/Scripts/python.exe -m app.main`, waits for `/api/health`, and
terminates only the child process it started.

## Checks

```powershell
pnpm typecheck     # TypeScript strict build
pnpm test:run      # Vitest + Testing Library (52 tests)
pnpm build         # production frontend bundle in dist/
pnpm tauri:build   # NSIS installer for the current user
```

Rust-only check:

```powershell
cd src-tauri
cargo check
```

## Production packaging

```powershell
cd apps\desktop
$env:PATH = "$env:USERPROFILE\.cargo\bin;$env:PATH"
pnpm tauri:build
```

The NSIS installer is written under `src-tauri/target/release/bundle/nsis/`.
To validate the shell without creating an installer:

```powershell
pnpm tauri build --debug --no-bundle
# binary: src-tauri/target/debug/mochi.exe
```

The frontend is served from the bundled `dist` directory inside the Tauri
window. No remote assets are required; fonts are bundled locally and the only
network origins allowed by the CSP are the loopback backend and the Vite dev
server.

### Backend in a packaged build

The shell looks for `backend/app/main.py` through `MOCHI_BACKEND_DIR`, the Tauri
resource directory, and bounded executable/manifest ancestors. It then starts
`backend/.venv/Scripts/python.exe -m app.main`.

The current installer does **not** embed a Python runtime or the backend venv,
so a packaged installation needs one of these:

1. Run the backend separately (recommended while developing), or
2. Point `MOCHI_BACKEND_DIR` at a machine that already has the backend and its
   venv, or
3. Freeze the backend with PyInstaller into a single executable and add it to
   `bundle.resources` in `src-tauri/tauri.conf.json`; the shell would then need
   a small spawn branch for that executable instead of the venv Python.

Until step 3 is implemented, the packaged UI honestly shows the backend as
offline when no local backend is available. It never fabricates a connection.

## Project layout

```
src/
  components/   App shell, mascot, composer, markdown, tool cards, approval dialog
  lib/          Typed API client, WebSocket stream, types, Zod schemas, helpers
  screens/      Home, Chat, Live Activity, Tasks, Computer, Files, Workflows,
                Memory, Schedule, Settings
  stores/       Zustand stores for app/chat/live/toast state
  test/         Vitest setup and unit/store tests
src-tauri/      Tauri 2 Rust shell, capabilities and icons
```

## Honest limitations

- No language model ships with Mochi. Configure Ollama or a provider key in
  **Settings → AI Models**; chat reasoning fails with a real backend error until
  then.
- GPU/VRAM telemetry is not exposed by the backend, so Mochi shows an explicit
  unavailable state instead of invented numbers.
- Voice input requires the optional `faster-whisper` backend package; TTS
  requires `pyttsx3`. The UI reports missing capabilities.
- Browser automation requires the backend's Playwright installation. If it is
  missing, opening a page returns a real error.
- Window telemetry (`GET /api/system/windows`) requires `pywin32` on the backend.
