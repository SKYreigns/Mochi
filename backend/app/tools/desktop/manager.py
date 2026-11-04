"""Windows desktop automation: windows, input, screenshots, OCR.

Uses pywinauto / Win32 APIs; PyAutoGUI is intentionally not required. All
destructive operations are classified in the tool layer, not here.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.core.errors import ToolExecutionError
from app.core.events import EventBus
from app.core.logging import get_logger

log = get_logger("desktop")

_PROTECTED_PROCESSES = {
    "system", "registry", "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe",
    "services.exe", "lsass.exe", "svchost.exe", "explorer.exe", "dwm.exe",
    "fontdrvhost.exe", "sihost.exe", "taskhostw.exe", "ctfmon.exe", "audiodg.exe",
    "spoolsv.exe", "searchindexer.exe",
}

_APP_ALIASES = {
    "chrome": ["chrome.exe"],
    "google chrome": ["chrome.exe"],
    "edge": ["msedge.exe"],
    "microsoft edge": ["msedge.exe"],
    "vscode": ["code.cmd", "code.exe"],
    "vs code": ["code.cmd", "code.exe"],
    "notepad": ["notepad.exe"],
    "calculator": ["calc.exe"],
    "paint": ["mspaint.exe"],
    "explorer": ["explorer.exe"],
    "file explorer": ["explorer.exe"],
    "word": ["winword.exe"],
    "excel": ["excel.exe"],
    "powerpoint": ["powerpnt.exe"],
    "cmd": ["cmd.exe"],
    "powershell": ["powershell.exe"],
    "terminal": ["wt.exe"],
}


class DesktopManager:
    def __init__(self, settings: Settings, events: EventBus) -> None:
        self.settings = settings
        self.events = events
        self.screenshots_dir = Path(settings.screenshots_dir or settings.data_dir / "screenshots")
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)

    # -- window inspection -------------------------------------------------
    def list_windows(self) -> list[dict[str, Any]]:
        try:
            import win32gui
            import win32process
        except ImportError as exc:
            raise ToolExecutionError("pywin32 is required for window inspection.", hint="pip install pywin32") from exc

        windows: list[dict[str, Any]] = []

        def callback(hwnd: int, _extra: Any) -> bool:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            title = win32gui.GetWindowText(hwnd)
            if not title.strip():
                return True
            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
            except Exception:
                pid = 0
            rect = win32gui.GetWindowRect(hwnd)
            windows.append(
                {
                    "handle": hwnd,
                    "title": title,
                    "class_name": win32gui.GetClassName(hwnd),
                    "pid": pid,
                    "rect": {"left": rect[0], "top": rect[1], "right": rect[2], "bottom": rect[3]},
                }
            )
            return True

        win32gui.EnumWindows(callback, None)
        return windows

    def active_window(self) -> dict[str, Any]:
        try:
            import win32gui
            import win32process
        except ImportError as exc:
            raise ToolExecutionError("pywin32 is required for window inspection.", hint="pip install pywin32") from exc
        hwnd = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd)
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            pid = 0
        rect = win32gui.GetWindowRect(hwnd)
        return {
            "handle": hwnd,
            "title": title,
            "class_name": win32gui.GetClassName(hwnd),
            "pid": pid,
            "process": _process_name(pid),
            "rect": {"left": rect[0], "top": rect[1], "right": rect[2], "bottom": rect[3]},
        }

    def focus_window(self, window_name: str) -> dict[str, Any]:
        try:
            from pywinauto import Desktop
        except ImportError as exc:
            raise ToolExecutionError("pywinauto is required to focus windows.", hint="pip install pywinauto") from exc
        try:
            window = Desktop(backend="uia").window(title_re=f".*{window_name}.*")
            window.wait("exists ready", timeout=8)
            window.set_focus()
            time.sleep(0.4)
        except Exception as exc:
            raise ToolExecutionError(
                f"Could not focus a window matching '{window_name}': {exc}",
                hint="Call desktop.list_windows to get the exact title first.",
            ) from exc
        active = self.active_window()
        if window_name.lower() not in active.get("title", "").lower():
            return {"requested": window_name, "active": active, "focused": False}
        return {"requested": window_name, "active": active, "focused": True}

    # -- application lifecycle --------------------------------------------
    def open_application(self, app_name: str) -> dict[str, Any]:
        name = app_name.strip().strip('"')
        if not name:
            raise ToolExecutionError("Application name is empty.")
        path = Path(name)
        if path.exists():
            try:
                os.startfile(str(path))  # type: ignore[attr-defined]
                return {"launched": str(path), "method": "startfile"}
            except OSError as exc:
                raise ToolExecutionError(f"Could not launch '{path}': {exc}") from exc

        candidates: list[str] = []
        lowered = name.lower()
        candidates.extend(_APP_ALIASES.get(lowered, []))
        if not candidates:
            candidates = [name, f"{name}.exe"]
        for candidate in candidates:
            resolved = shutil.which(candidate)
            if resolved:
                try:
                    process = subprocess.Popen([resolved], close_fds=True)
                    time.sleep(0.6)
                    return {"launched": resolved, "pid": process.pid, "method": "process"}
                except OSError as exc:
                    raise ToolExecutionError(f"Could not start '{resolved}': {exc}") from exc
        raise ToolExecutionError(
            f"Application '{name}' was not found.",
            hint="Provide a full path, or install the app and use its executable name (e.g. notepad.exe).",
        )

    def close_application(self, app_name: str) -> dict[str, Any]:
        try:
            import psutil
        except ImportError as exc:
            raise ToolExecutionError("psutil is required.") from exc
        needle = app_name.lower().replace(".exe", "")
        targets = []
        for process in psutil.process_iter(["pid", "name", "exe"]):
            process_name = (process.info.get("name") or "").lower()
            if process_name in _PROTECTED_PROCESSES:
                continue
            if needle in process_name or needle in (process.info.get("exe") or "").lower():
                targets.append(process)
        if not targets:
            raise ToolExecutionError(f"No running process matched '{app_name}'.")
        terminated = []
        for process in targets:
            try:
                process.terminate()
                terminated.append({"pid": process.pid, "name": process.info.get("name")})
            except Exception as exc:
                log.warning("Terminate failed for %s: %s", process.pid, exc)
        psutil.wait_procs(targets, timeout=6)
        still_running = [p.info.get("name") for p in targets if p.is_running()]
        return {"terminated": terminated, "still_running": still_running}

    # -- input -------------------------------------------------------------
    def click(self, x: int, y: int, *, double: bool = False) -> dict[str, Any]:
        try:
            from pywinauto import mouse
        except ImportError as exc:
            raise ToolExecutionError("pywinauto is required for mouse control.") from exc
        if not (0 <= x <= 20000 and 0 <= y <= 20000):
            raise ToolExecutionError("Screen coordinates are out of range.")
        before = self.active_window()
        mouse.click(button="left", coords=(x, y), double=double)
        time.sleep(0.35)
        return {"x": x, "y": y, "double": double, "active_before": before.get("title"), "active_after": self.active_window().get("title")}

    def type_text(self, text: str) -> dict[str, Any]:
        try:
            from pywinauto import keyboard
        except ImportError as exc:
            raise ToolExecutionError("pywinauto is required for keyboard input.") from exc
        if len(text) > 10_000:
            raise ToolExecutionError("Text is too long for a single keyboard operation.")
        escaped = _escape_send_keys(text)
        keyboard.send_keys(escaped, with_spaces=True, with_tabs=True, with_newlines=True, pause=0.01)
        return {"characters": len(text), "active": self.active_window().get("title")}

    def press_key(self, key: str) -> dict[str, Any]:
        try:
            from pywinauto import keyboard
        except ImportError as exc:
            raise ToolExecutionError("pywinauto is required for keyboard input.") from exc
        sequence = _key_sequence(key)
        keyboard.send_keys(sequence, pause=0.02)
        return {"key": key, "sequence": sequence, "active": self.active_window().get("title")}

    def hotkey(self, keys: list[str]) -> dict[str, Any]:
        if not keys:
            raise ToolExecutionError("No keys were provided.")
        normalized = [key.strip().lower() for key in keys if key.strip()]
        modifiers = ""
        for key in normalized[:-1]:
            if key in ("ctrl", "control"):
                modifiers += "^"
            elif key == "alt":
                modifiers += "%"
            elif key == "shift":
                modifiers += "+"
            else:
                raise ToolExecutionError(f"Unsupported modifier '{key}'.")
        main_key = _key_sequence(normalized[-1]).strip("{}")
        sequence = f"{modifiers}{{{main_key}}}"
        from pywinauto import keyboard

        keyboard.send_keys(sequence, pause=0.02)
        return {"keys": normalized, "sequence": sequence, "active": self.active_window().get("title")}

    def scroll(self, direction: str, amount: int) -> dict[str, Any]:
        try:
            import win32api
            from pywinauto import mouse
        except ImportError as exc:
            raise ToolExecutionError("pywinauto and pywin32 are required for scrolling.") from exc
        x, y = win32api.GetCursorPos()
        distance = max(1, min(50, amount)) * (1 if direction == "up" else -1)
        mouse.scroll(coords=(x, y), wheel_dist=distance)
        return {"direction": direction, "amount": amount, "at": [x, y]}

    # -- capture / OCR -----------------------------------------------------
    def screenshot(self, monitor: int = 0) -> dict[str, Any]:
        try:
            import mss
        except ImportError as exc:
            raise ToolExecutionError("mss is required for screenshots.") from exc
        filename = f"desktop_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.png"
        target = self.screenshots_dir / filename
        with mss.mss() as capture:
            monitors = capture.monitors
            if monitor <= 0 or monitor >= len(monitors):
                monitor = 0
            region = monitors[monitor]
            shot = capture.grab(region)
            shot.save(str(target))
        return {
            "path": str(target),
            "url": f"/api/live/screenshots/{filename}",
            "size": target.stat().st_size,
            "monitor": monitor,
            "monitors": len(monitors) - 1,
        }

    def get_screen_text(self, monitor: int = 0) -> dict[str, Any]:
        try:
            import pytesseract
            from PIL import Image
        except ImportError as exc:
            raise ToolExecutionError("pytesseract and Pillow are required for OCR.") from exc
        tesseract_path = self.settings.tesseract_cmd or shutil.which("tesseract")
        if not tesseract_path:
            for candidate in (
                r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            ):
                if Path(candidate).exists():
                    tesseract_path = candidate
                    break
        if not tesseract_path:
            raise ToolExecutionError(
                "Tesseract OCR is not installed.",
                hint="Install it (winget install UB-Mannheim.TesseractOCR) or set JARVIS_TESSERACT_CMD, then retry.",
            )
        pytesseract.pytesseract.tesseract_cmd = tesseract_path
        shot = self.screenshot(monitor)
        try:
            text = pytesseract.image_to_string(Image.open(shot["path"]))
        except Exception as exc:
            raise ToolExecutionError(f"OCR failed: {exc}") from exc
        return {"text": text[:20000], "screenshot": shot, "chars": len(text)}
        # Note: no OCR result is fabricated when the engine is missing.

    def get_running_processes(self, limit: int = 60) -> list[dict[str, Any]]:
        try:
            import psutil
        except ImportError as exc:
            raise ToolExecutionError("psutil is required.") from exc
        processes: list[dict[str, Any]] = []
        for process in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info", "username"]):
            try:
                info = process.info
                memory = info.get("memory_info")
                processes.append(
                    {
                        "pid": info["pid"],
                        "name": info.get("name") or "",
                        "cpu_percent": info.get("cpu_percent") or 0.0,
                        "memory_mb": round((memory.rss if memory else 0) / (1024 * 1024), 1),
                        "user": info.get("username") or "",
                    }
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        processes.sort(key=lambda item: item["memory_mb"], reverse=True)
        return processes[:limit]


def _process_name(pid: int) -> str:
    try:
        import psutil

        return psutil.Process(pid).name()
    except Exception:
        return ""


def _escape_send_keys(text: str) -> str:
    # pywinauto treats {} + ^ % ~ ( ) as modifiers/sequences.
    replacements = {
        "{": "{{}", "}": "{}}", "+": "{+}", "^": "{^}", "%": "{%}", "~": "{~}",
        "(": "{(}", ")": "{)}", "[": "{[}", "]": "{]}",
    }
    return "".join(replacements.get(char, char) for char in text)


def _key_sequence(key: str) -> str:
    aliases = {
        "enter": "{ENTER}", "return": "{ENTER}", "tab": "{TAB}", "esc": "{ESC}",
        "escape": "{ESC}", "space": "{SPACE}", "backspace": "{BACKSPACE}",
        "delete": "{DELETE}", "del": "{DELETE}", "home": "{HOME}", "end": "{END}",
        "pageup": "{PGUP}", "page down": "{PGDN}", "pagedown": "{PGDN}",
        "up": "{UP}", "down": "{DOWN}", "left": "{LEFT}", "right": "{RIGHT}",
        "f1": "{F1}", "f2": "{F2}", "f3": "{F3}", "f4": "{F4}", "f5": "{F5}",
        "f6": "{F6}", "f7": "{F7}", "f8": "{F8}", "f9": "{F9}", "f10": "{F10}",
        "f11": "{F11}", "f12": "{F12}", "win": "{LWIN}",
    }
    lowered = key.strip().lower()
    if lowered in aliases:
        return aliases[lowered]
    if "+" in key or "^" in key or "%" in key:
        return key
    return f"{{{key.upper()}}}"
