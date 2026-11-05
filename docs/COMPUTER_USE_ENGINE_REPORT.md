# Computer Use Engine Report (Milestone 3)

Date: 2026-11-05
Branch: `main` (baseline commit `4dd1948`)
Scope: a safe, reliable Windows Computer Use Engine for Mochi: observe → plan → validate →
act → re-observe → verify, with persistent STOP, approval gates, planner/completion
validation, scoped events, and live Notepad verification. No frontend redesign, no paid
APIs, no cloud inference, and no new mandatory external service.

## 1. Executive summary

| Area | Result |
|---|---|
| Backend test suite | **165 passed** (`pytest -q`, 85.9 s) |
| Frontend test suite | **62 passed** (`pnpm test:run`, 4 files) |
| Typecheck / lint / build | **exit 0** / **0 errors (warnings only)** / **exit 0** |
| Live Notepad verification | **All 17 recorded steps passed** on a dedicated temporary document |
| Persistent STOP | Survives backend restart, blocks input, requires explicit resume — verified live |
| Approval gate | Existing mandatory approval rules retained; STOP also rejects pending approvals |
| Evidence-based completion | Claimed actions are checked against successful execution records; unverified computer actions add a caveat |
| OCR | **Unavailable** — Tesseract executable is not installed; reported, not mocked |

The engine reuses the existing `DesktopManager` (windows, screenshots, low-level input),
the permission engine, the event bus and the SQLite database. A new `backend/app/computer/`
package adds observation, input safety, verification, an orchestration engine and 18
`computer.*` tools. No schema migration was needed: emergency STOP is persisted through
`ApplicationSetting` plus `AuditLog`.

## 2. Scope and environment

- Backend: FastAPI, SQLAlchemy async, SQLite, `127.0.0.1:8765`.
- Frontend: Vite/React/Tauri desktop shell, `127.0.0.1:1420`.
- Host: Windows 11, session 2, medium integrity (no elevation).
- Live apps used: Windows 11 Notepad (`Microsoft.WindowsNotepad 11.2607.14.0`), a controlled
  WinForms input probe, and the real desktop.
- Input libraries: `pywinauto 0.6.9`, `pywin32`, `comtypes 1.4.17`, `mss 10.2.0`,
  `Pillow 13.0.0`; `pytesseract` is installed but the Tesseract executable is not.
- Live artifacts (outside the repo):
  `%TEMP%\mochi-computer-use-as3swfgt\mochi_m3_dbee6ec7.txt`,
  `%TEMP%\opencode\live_check10.log`, and desktop screenshots under the app's data dir.
- Verification text used in the final live run: `MOCHI_M3_FE9541D8A0`.

## 3. Architecture

`backend/app/computer/`

| Module | Responsibility |
|---|---|
| `observer.py` | Read-only observation: windows, UI Automation tree, screenshot, optional OCR. |
| `input.py` | Guarded mouse/keyboard/clipboard injection; shell/elevation hotkey blocks; one last STOP check before every event. |
| `verification.py` | Active-window, UIA text/element, clipboard and OCR verification. |
| `engine.py` | Action orchestration, target-window guard, activity state, event publishing. |
| `tools.py` | 18 `computer.*` tool registrations in the existing registry/permission system. |

Flow for an action:

1. **OBSERVE** — capture the active window, UIA elements and (optionally) a screenshot.
2. **PLAN** — the existing planner produces steps; `orchestrator/planner.py` prunes unknown or
   unrelated tools and duplicate steps, and tracks used/unused steps.
3. **VALIDATE** — the permission engine decides observe/assisted/autonomous rules; STOP and
   approvals are checked before execution; consequential labels are screened.
4. **ACT** — input is injected only after a fresh STOP check and a target-window check.
5. **RE-OBSERVE** — read the window/UIA/clipboard again from a new observation.
6. **VERIFY** — only observed evidence marks an action verified; a click or a generated
   response alone never does.

Two API endpoints expose the engine read-only: `GET /api/computer/state` and
`GET /api/computer/observe`. Observation is allowed in every permission mode.

## 4. Persistent STOP and restart recovery

- Persisted key: `safety.emergency` in `ApplicationSetting`; each change writes an `AuditLog`
  row (`safety.emergency_stop`, `safety.emergency_resume`, recovery events).
- Startup reconciliation is fail-safe: stale running tasks, executions or approvals force STOP
  instead of silently resuming work.
- STOP blocks every input action in the permission engine and in `InputController` itself.
- Pending approvals are rejected on STOP and require a new explicit approval after resume.
- Only `POST /api/tasks/resume` clears STOP; there is no automatic resumption.
- The UI already exposes STOP and resume in Settings, the global shell banner, and Live
  Activity; the Computer screen now also shows the STOP reason and the explicit-resume rule.

Live evidence (`live_check10.log`):

| Step | Result |
|---|---|
| `emergency_stop` | `stopped: true` |
| `input_blocked_while_stopped` | `blocked: true`, document unchanged |
| `resume` | `stopped: false` (explicit call only) |
| `restart_reconcile` | `stopped: true` after rebuilding `AppServices` |
| `input_blocked_after_restart` | `blocked: true`, document unchanged |
| `restart_explicit_resume` | `stopped: false` |

## 5. Approval gates and consequential actions

- Sending messages, submitting forms, purchases, file deletion, software installation, sharing
  private information, security changes and elevated operations remain approval-gated through
  the existing permission engine and approval queue.
- `computer.click_element` screens element labels against a consequential-action regex and
  refuses `send/submit/buy/pay/delete/install/share/confirm/...` unless the explicit
  `computer.consequential_click` path with an approval is used.
- The input controller refuses the Windows Run dialog and shell/elevation hotkeys
  (`Win+R`, `Win+X`, `Ctrl+Shift+Enter`) so computer use cannot silently become a shell.
- Keyboard/browser/shell bypasses were not added; STOP and the approval engine are enforced
  in the same execution path.

## 6. Observation, UI Automation, OCR and untrusted data

- Observation always marks its output `untrusted: true`. Screenshots and UI text are data,
  never instructions.
- UI Automation is preferred for element discovery and clicking; coordinate clicks exist but
  report their outcome as unverified unless a subsequent observation changes the window or
  element state.
- `_uia_text_for_window` now verifies text against the **target window handle**, not a mutable
  title. This matters because editors prefix the title with `*` as soon as a document becomes
  dirty (`*Untitled - Notepad`).
- OCR is optional. `pytesseract` is present but Tesseract is not installed, so
  `capabilities.ocr` is `false`; observations include an explicit warning. No OCR result was
  fabricated.

## 7. Input reliability (Windows 11 Notepad findings)

The live investigation found several real Windows/WinUI3 behaviors and fixes:

1. **Stuck modifiers.** `pywinauto.send_keys("^{A}")` typed a literal `A` and could leave
   modifiers down. The controller now emits Win32 key events directly and always releases
   modifiers in `finally`.
2. **Stray key-up events drop the next character.** Sending a key-up for a modifier that was
   never down made WinUI3 Notepad drop the first character. `_release_modifiers()` now checks
   `GetAsyncKeyState` and only releases keys that are actually down.
3. **`SendInput` struct layout.** `_INPUT` was 32 bytes instead of Windows' 40 bytes, causing
   `SendInput` to fail with error 87. The `MOUSEINPUT`/`HARDWAREINPUT` union members restore
   the correct 40-byte layout, and `argtypes`/`restype` are set explicitly.
4. **WinUI3 focus.** Notepad only accepts synthetic input when the real Document control holds
   UIA focus and the injecting thread is attached to the input queue. Typing now uses
   `UIAWrapper.type_keys` on the sole Document/Edit control (with focus and thread
   attachment), falling back to a Win32 SendInput path only when no single text control exists.
5. **Fast injection corrupts characters.** With a 5 ms per-character pause, `04738C` was
   silently written as `CCCCCC`. A 20 ms pause was correct in repeated trials; long text
   (> 400 characters) now uses one atomic clipboard paste instead, restoring the previous
   clipboard afterwards.
6. **Foreground restoration.** `DesktopManager.focus_window` now restores minimized windows
   and uses `ShowWindow`/`BringWindowToTop`/`SetForegroundWindow` with thread attachment.
7. **Window handles beat titles.** Verification uses the captured handle so post-typing title
   changes cannot redirect or break verification.

## 8. Planner and completion validation

- **Planner validation** (`orchestrator/planner.py`): unknown tools, unrelated tools and
  duplicate steps are pruned; the agent tracks which plan steps were executed and which were
  left unused.
- **Completion validation** (`orchestrator/validation.py`): the final answer's claimed action
  categories are compared with successful execution records. Claims without evidence are
  flagged; unverified computer actions add an explicit caveat instead of being reported as
  done.
- **Retries**: the agent retries a failed step with the observation/verification detail
  instead of assuming success.
- Regression coverage lives in `backend/tests/test_phase1_reliability.py` (9 tests) and
  `backend/tests/test_computer_engine.py` (21 tests).

## 9. UI and scoped events

- The existing Computer screen was **extended, not redesigned**, with a "Computer use engine"
  card showing:
  - engine name, permission mode and capability badges;
  - persistent STOP state and reason;
  - current/last activity and detail;
  - last verification (verified/unverified, method, evidence);
  - last observation (active window, window/element counts, detection sources, warnings);
  - an "Observe desktop" button (read-only).
- New API client methods: `api.computerState()` and `api.computerObserve()`.
- WebSocket events are scoped with `?task_id=` / `?conversation_id=` query parameters and
  filtered for both replayed and live events, so a task no longer receives unrelated computer
  events.
- Frontend tests: 62 passed, including two new screen tests for ready/verified and STOP states.

## 10. Live verification, limitations and test matrix

### Live Notepad run (2026-11-05 17:34)

Dedicated temp document: `%TEMP%\mochi-computer-use-as3swfgt\mochi_m3_dbee6ec7.txt`.
Before every input step the script re-read the selected tab and required it to be that
document. All steps passed:

| Step | Result |
|---|---|
| Launch + focus dedicated document | title `mochi_m3_dbee6ec7.txt - Notepad` |
| Observe | 18 windows, 60 UIA elements, screenshot captured |
| Type + verify | `verified: true`, method `uia`, text present |
| Select all + copy + clipboard | `verified: true`, clipboard matched |
| Paste + verify | `verified: true`, replacement text present |
| Type tail + verify | `verified: true` |
| STOP blocks input | `blocked: true`, document unchanged |
| Explicit resume + type | `verified: true` |
| Restart → STOP restored | `stopped: true` |
| Input blocked after restart | `blocked: true`, document unchanged |
| Explicit resume after restart | `stopped: false` |

### Test matrix

| Suite | Command | Result |
|---|---|---|
| Backend | `cd backend; .\.venv\Scripts\python.exe -m pytest -q` | 165 passed |
| Frontend | `cd apps/desktop; pnpm test:run` | 62 passed |
| Typecheck | `pnpm typecheck` | exit 0 |
| Lint | `pnpm lint` | 0 errors (warnings pre-existing) |
| Build | `pnpm build` | exit 0 |

### Honest limitations

- **OCR is not available.** Tesseract must be installed separately; OCR-based verification
  remains untested and is reported as unavailable rather than simulated.
- **Coordinate clicks are only injection-verified.** Without a resulting UIA/window change,
  the engine reports the outcome as unconfirmed.
- **UIA typing is scoped.** It is used only when the active window exposes exactly one
  Document/Edit control. Windows with several fields fall back to SendInput, where WinUI3
  editors can still drop or garble fast characters; verification flags that outcome.
- **Long text uses the clipboard.** This temporarily replaces the clipboard (restored
  immediately) and may interact with clipboard managers.
- **Notepad target-isolation incident.** An early live script typed into a restored user
  Notepad tab (`matrix_analysis.txt`) before the isolation guard existed. The tool did not
  save that document, but the Notepad process later exited and unsaved tab state cannot be
  guaranteed to have survived. Subsequent runs used a dedicated temp file and verified the
  selected tab before every input. This is recorded here rather than hidden.
- **Python 3.15 / comtypes warning.** `pywinauto`/`comtypes` emit a `FutureWarning` about
  enum behavior on Python 3.15; functionality was unaffected.
- **Not every real-world app was exercised live.** Browsers, multi-field forms, terminals and
  elevated apps were not driven end-to-end; they remain covered by unit-level guards and
  simulated tests only.

## Status

### Verified

- Persistent emergency STOP across restart; fail-safe reconciliation; audit entries.
- Input blocking while stopped; explicit-resume-only recovery.
- Live Notepad typing, select-all, copy, clipboard, paste and UIA text verification on a
  dedicated temporary document.
- Window-handle-based verification after title changes.
- Approval gate, STOP approval rejection, consequential-label screening.
- Planner pruning/step tracking and completion validation.
- Scoped WebSocket events.
- Backend 165 tests, frontend 62 tests, typecheck, lint and build.

### Partially working

- Coordinate clicks/drags: injected and recorded, but the outcome requires a later observation
  to be considered verified.
- UI text typing in windows with multiple text controls: falls back to SendInput and can be
  less reliable than the UIA path.
- Long text: typed via atomic clipboard paste rather than individual keystrokes.
- OCR absence means visual verification relies on UIA and screenshots only.

### Not implemented

- Installing/relying on Tesseract OCR.
- Full live coverage for browsers, multi-field forms, terminals and elevated applications.
- Any cloud/paid vision model or cloud inference.

### Failed / caveats

- No live test failed in the final run.
- Earlier iterations failed and were fixed: dropped first characters, `SendInput` struct size,
  stuck modifiers, title-based verification, and pywinauto `send_keys` mis-typing `Ctrl+A`.
- The early Notepad target-isolation incident is a real safety caveat (see section 10).
