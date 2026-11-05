"""Live Windows computer-use check in a dedicated Notepad document.

Safety model
------------
* A unique temporary ``.txt`` file is created; Notepad is launched with it.
* Before every input step the selected Notepad tab is re-read through UI
  Automation and must still be the temporary file created by this script.
* Only that tab is ever selected. Existing tabs/documents are never closed,
  saved, overwritten or deleted.
* The test writes only to the temporary file, which is left on disk for
  inspection.
* The clipboard is saved before the test and restored afterwards.

Run from ``backend``::

    .venv\\Scripts\\python.exe scripts\\computer_use_live_check.py
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings  # noqa: E402
from app.core.container import AppServices  # noqa: E402
from app.core.errors import EmergencyStopError  # noqa: E402

UNIQUE = f"MOCHI_M3_{uuid.uuid4().hex[:10].upper()}"


def _notepad_window(handle: int | None = None):
    from pywinauto import Desktop

    desktop = Desktop(backend="uia")
    if handle:
        try:
            return desktop.window(handle=handle)
        except Exception:
            return None
    for window in desktop.windows(visible_only=True):
        try:
            title = window.window_text()
        except Exception:
            continue
        if "Notepad" in title:
            return window
    return None


def _selected_tabs(window) -> list[str]:
    try:
        return [c.window_text() for c in window.descendants(control_type="TabItem") if c.is_selected()]
    except Exception:
        return []


def _document_text(window) -> str:
    try:
        controls = list(window.descendants(control_type="Document"))
    except Exception:
        return ""
    for control in controls:
        for getter in (
            lambda: control.get_value(),
            lambda: control.window_text(),
        ):
            try:
                value = getter()
            except Exception:
                continue
            if value is not None:
                return str(value)
    return ""


def _wait_for_document(stem: str, timeout: float = 15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for window in _all_notepad_windows():
            if any(stem in tab for tab in _selected_tabs(window)):
                return window
        time.sleep(0.3)
    return None


def _all_notepad_windows():
    from pywinauto import Desktop

    found = []
    for window in Desktop(backend="uia").windows(visible_only=True):
        try:
            title = window.window_text()
        except Exception:
            continue
        if "Notepad" in title:
            found.append(window)
    return found


async def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="mochi-computer-use-"))
    token = uuid.uuid4().hex[:8]
    document = root / f"mochi_m3_{token}.txt"
    baseline = f"BASELINE {UNIQUE}"
    document.write_text(baseline, encoding="utf-8")

    database = root / "live.db"
    settings = Settings(
        environment="test",
        data_dir=root / "data",
        log_dir=root / "logs",
        database_url=f"sqlite+aiosqlite:///{database.as_posix()}",
        llm_provider="mock",
        permission_mode="assisted",
    )
    services = AppServices(settings)
    await services.init()
    engine = services.computer
    results: dict[str, object] = {
        "temp_dir": str(root),
        "document": str(document),
        "unique_text": UNIQUE,
        "steps": [],
    }

    def record(step: str, **payload: object) -> None:
        results["steps"].append({"step": step, **payload})
        print(json.dumps({"step": step, **payload}, default=str, ensure_ascii=False))

    clipboard_backup = ""
    try:
        clipboard_backup = engine.input.get_clipboard()

        notepad = shutil.which("notepad.exe")
        if not notepad:
            record("abort", reason="notepad.exe was not found.")
            return 2
        process = subprocess.Popen([notepad, str(document)])
        window = _wait_for_document(document.stem)
        record(
            "launch",
            pid=process.pid,
            document=str(document),
            window_title=(window.window_text() if window else None),
            selected_tabs=(_selected_tabs(window) if window else []),
        )
        if window is None:
            record("abort", reason="The dedicated Notepad document did not appear.")
            return 3

        await engine.focus_window(document.stem)
        time.sleep(0.4)
        state = await engine.state()
        handle = int((state.get("active_window") or {}).get("handle") or 0)
        record("focus", focused=handle != 0, active_title=(state.get("active_window") or {}).get("title"))
        if not handle:
            raise RuntimeError("No active window handle was available.")

        def guard(label: str) -> str:
            live = _notepad_window(handle)
            if live is None:
                raise RuntimeError(f"Notepad window disappeared before {label}.")
            tabs = _selected_tabs(live)
            if not any(document.stem in tab for tab in tabs):
                raise RuntimeError(f"Refusing to continue: selected tab is not the test document ({tabs}).")
            return _document_text(live)

        guard("observation")
        observation = await engine.observe(include_elements=True, include_screenshot=True)
        record(
            "observe",
            active_window=observation["active_window"].get("title"),
            window_count=observation["window_count"],
            element_count=observation["element_count"],
            sources=observation["detection_sources"],
            screenshot=(observation.get("screenshot") or {}).get("url"),
            warnings=observation.get("warnings"),
        )

        expected_typed = f"TYPED {UNIQUE}"
        typed = await engine.type_text(expected_typed, window_handle=handle, verify=True)
        after_typed = guard("typing verification")
        record(
            "type_and_verify",
            verified=bool(typed["verification"].get("verified")),
            method=typed["verification"].get("method"),
            detail=typed["verification"].get("detail"),
            characters=typed["input"].get("characters"),
            document_contains=expected_typed in after_typed,
            document=after_typed[:200],
        )

        await engine.select_all()
        clipboard_expected = guard("copy")
        copied = await engine.copy(expected=clipboard_expected)
        record(
            "select_all_copy_clipboard",
            verified=bool((copied.get("verification") or {}).get("verified")),
            clipboard_matches=(engine.input.get_clipboard() == clipboard_expected),
            detail=(copied.get("verification") or {}).get("detail"),
        )

        replacement = f"PASTED {UNIQUE}"
        await engine.select_all()
        pasted = await engine.paste_text(replacement, window_handle=handle, verify=True)
        after_paste = guard("paste verification")
        record(
            "paste_and_verify",
            verified=bool(pasted["verification"].get("verified")),
            method=pasted["verification"].get("method"),
            detail=pasted["verification"].get("detail"),
            document_contains=(replacement in after_paste),
            document=after_paste[:200],
        )

        tail_text = " TAIL"
        tail = await engine.type_text(tail_text, window_handle=handle, verify=True)
        after_tail = guard("tail verification")
        record(
            "type_tail",
            verified=bool(tail["verification"].get("verified")),
            document_contains=((replacement + tail_text) in after_tail),
            document=after_tail[:200],
        )

        stop = await services.tasks.emergency_stop("live Notepad safety test")
        record("emergency_stop", stopped=stop.get("stopped"), reason=stop.get("reason"))
        blocked = False
        try:
            await engine.type_text("THIS MUST NOT BE TYPED", window_handle=handle)
        except EmergencyStopError:
            blocked = True
        record(
            "input_blocked_while_stopped",
            blocked=blocked,
            document_unchanged=("THIS MUST NOT BE TYPED" not in guard("stop check")),
        )

        resumed = await services.tasks.resume("live test explicit resume")
        record("resume", stopped=resumed.get("stopped"))
        resumed_text = f" RESUMED {UNIQUE[:6]}"
        after_resume = await engine.type_text(resumed_text, window_handle=handle, verify=True)
        record(
            "typing_after_resume",
            verified=bool(after_resume["verification"].get("verified")),
            detail=after_resume["verification"].get("detail"),
            document=guard("resume verification")[:200],
        )

        final_state = await engine.state()
        record(
            "final_state",
            activity=final_state.get("activity"),
            last_verification=final_state.get("last_verification"),
            last_observation=final_state.get("last_observation"),
            emergency=final_state.get("emergency"),
        )

        # --- restart persistence -------------------------------------------
        await services.tasks.emergency_stop("restart persistence test")
        before_restart = guard("restart stop")
        await services.db.dispose()
        restarted = AppServices(settings)
        await restarted.init()
        record(
            "restart_reconcile",
            stopped=restarted.emergency.stopped,
            emergency=restarted.emergency.snapshot(),
        )
        blocked_after_restart = False
        try:
            await restarted.computer.type_text("MUST NOT TYPE AFTER RESTART", window_handle=handle)
        except EmergencyStopError:
            blocked_after_restart = True
        record(
            "input_blocked_after_restart",
            blocked=blocked_after_restart,
            document_unchanged=("MUST NOT TYPE AFTER RESTART" not in guard("restart input check")),
        )
        resumed_after_restart = await restarted.tasks.resume("restart test explicit resume")
        record(
            "restart_explicit_resume",
            stopped=resumed_after_restart.get("stopped"),
            emergency=restarted.emergency.snapshot(),
        )
        await restarted.db.dispose()
        record("restart_document_unchanged", document_snippet=before_restart[:200])

        record(
            "done",
            note="All typing went to the dedicated temporary Notepad document; it was left open and unsaved.",
        )
    except Exception as exc:  # noqa: BLE001 - the live harness records the failure
        record("error", error_type=type(exc).__name__, error=str(exc))
        return 1
    finally:
        try:
            if clipboard_backup is not None:
                engine.input.set_clipboard(clipboard_backup)
        except Exception:
            pass
        try:
            await services.db.dispose()
        except Exception:
            pass

    print("\n=== SUMMARY ===")
    print(json.dumps(results, default=str, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
