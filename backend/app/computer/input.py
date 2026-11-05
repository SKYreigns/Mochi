"""Controlled mouse, keyboard and clipboard input.

The input controller never decides permissions; the tool/permission layer does.
It does enforce two non-negotiable safety rules of its own:

* one last emergency-STOP check immediately before injecting input, and
* refusal to drive the Windows "Run" dialog or shell-launch hotkeys, so
  computer use can never silently become unrestricted shell access.
"""

from __future__ import annotations

import ctypes
import time
from contextlib import contextmanager
from ctypes import wintypes
from typing import Any, Iterator

from app.core.errors import EmergencyStopError, ToolExecutionError
from app.core.logging import get_logger

log = get_logger("computer.input")

_INPUT_KEYBOARD = 1
_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_UNICODE = 0x0004


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

_BLOCKED_HOTKEYS = {
    ("win", "r"),
    ("win", "x"),
    ("win", "cmd"),
    ("ctrl", "shift", "enter"),
}

_BLOCKED_KEY_SEQUENCES = {
    "win+r",
    "win+x",
    "win+cmd",
}

_SHELL_TITLES = {"run", "运行"}
_SHELL_PROCESSES = {"cmd.exe", "powershell.exe", "pwsh.exe", "wt.exe", "windowsterminal.exe"}

_VK = {
    "ctrl": 0x11,
    "control": 0x11,
    "shift": 0x10,
    "alt": 0x12,
    "win": 0x5B,
    "lwin": 0x5B,
    "enter": 0x0D,
    "return": 0x0D,
    "tab": 0x09,
    "esc": 0x1B,
    "escape": 0x1B,
    "space": 0x20,
    "backspace": 0x08,
    "delete": 0x2E,
    "del": 0x2E,
    "insert": 0x2D,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
    "capslock": 0x14,
    **{f"f{number}": 0x6F + number for number in range(1, 13)},
    **{letter: ord(letter.upper()) for letter in "abcdefghijklmnopqrstuvwxyz"},
    **{str(number): ord(str(number)) for number in range(10)},
    ";": 0xBA,
    "=": 0xBB,
    ",": 0xBC,
    "-": 0xBD,
    ".": 0xBE,
    "/": 0xBF,
    "`": 0xC0,
    "[": 0xDB,
    "\\": 0xDC,
    "]": 0xDD,
    "'": 0xDE,
}

_MODIFIER_VKS = {0x11, 0x10, 0x12, 0x5B}


def _keybd_event(vk: int, *, up: bool = False) -> None:
    import win32api
    import win32con

    flags = win32con.KEYEVENTF_KEYUP if up else 0
    win32api.keybd_event(vk, 0, flags, 0)


def _release_modifiers() -> None:
    """Defensive: clear modifiers a previous automation call may have left down.

    Only keys that Windows reports as physically down are released. Sending a
    key-up for a key that was never pressed injects a stray event and makes
    WinUI3 editors drop the first character of the following text (observed on
    Windows 11 Notepad), so the state check is required, not an optimisation.
    """
    serialized_events = False
    for vk in (0x11, 0x10, 0x12, 0x5B):
        try:
            import win32api

            if not (win32api.GetAsyncKeyState(vk) & 0x8000):
                continue
            _keybd_event(vk, up=True)
            serialized_events = True
        except Exception:
            continue
    if serialized_events:
        # Give the target input queue a moment to process the releases before
        # real characters arrive; otherwise the first one can be swallowed.
        time.sleep(0.08)


@contextmanager
def _attached_to_foreground() -> Iterator[int]:
    """Attach to the foreground thread while injecting input.

    Modern WinUI3 applications (including Windows 11 Notepad) ignore synthetic
    keyboard events unless the injecting thread is attached to the foreground
    window's input queue. Pywinauto does this inside ``type_keys``; the engine
    must do the same for its Win32 SendInput path.
    """
    import win32api
    import win32gui
    import win32process

    current = win32api.GetCurrentThreadId()
    foreground = win32gui.GetForegroundWindow()
    target = 0
    attached = False
    try:
        target, _ = win32process.GetWindowThreadProcessId(foreground)
        if target and target != current:
            attached = bool(win32process.AttachThreadInput(current, target, True))
    except Exception:
        attached = False
    try:
        yield foreground
    finally:
        if attached:
            try:
                win32process.AttachThreadInput(current, target, False)
            except Exception:
                pass


def _resolve_vk(key: str) -> int | None:
    normalized = key.strip().lower()
    if not normalized:
        return None
    if normalized in _VK:
        return _VK[normalized]
    if len(normalized) == 1:
        # Single printable characters: prefer the explicit map, otherwise ask
        # Windows for the virtual-key code (handles punctuation/layouts).
        try:
            import win32api

            scan = win32api.VkKeyScan(normalized)
            if scan != -1:
                return int(scan) & 0xFF
        except Exception:
            return None
    return None


def _tap_key(vk: int) -> None:
    _keybd_event(vk)
    _keybd_event(vk, up=True)


def _send_unicode(code_unit: int) -> None:
    """Inject one UTF-16 code unit with SendInput.

    ``keybd_event`` is layout dependent and cannot emit arbitrary Unicode. The
    keyboard ``SendInput`` path uses ``KEYEVENTF_UNICODE`` so the exact text is
    delivered regardless of the active keyboard layout.
    """
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SendInput.argtypes = (
        wintypes.UINT,
        ctypes.POINTER(_INPUT),
        ctypes.c_int,
    )
    user32.SendInput.restype = wintypes.UINT
    events = (_INPUT * 2)()
    for index, flags in enumerate((_KEYEVENTF_UNICODE, _KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP)):
        events[index].type = _INPUT_KEYBOARD
        events[index].u.ki = _KEYBDINPUT(0, code_unit, flags, 0, None)
    sent = user32.SendInput(
        2, ctypes.cast(events, ctypes.POINTER(_INPUT)), ctypes.sizeof(_INPUT)
    )
    if sent != 2:
        raise ToolExecutionError(
            "Windows rejected synthetic keyboard input. The target window may be elevated or protected.",
            hint=(
                "Focus a normal (non-elevated) window. Computer use must never bypass Windows "
                "UIPI/elevation boundaries."
            ),
        )


def _type_text_win32(text: str) -> int:
    sent = 0
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\r":
            if index + 1 < len(text) and text[index + 1] == "\n":
                index += 1
            _tap_key(0x0D)
            sent += 1
        elif char == "\n":
            _tap_key(0x0D)
            sent += 1
        elif char == "\t":
            _tap_key(0x09)
            sent += 1
        else:
            encoded = char.encode("utf-16-le")
            for offset in range(0, len(encoded), 2):
                code_unit = int.from_bytes(encoded[offset : offset + 2], "little")
                _send_unicode(code_unit)
                sent += 1
            # A short pause keeps fast consecutive Unicode events ordered in
            # editors that debounce asynchronous accessibility updates.
            time.sleep(0.01)
        index += 1
    return sent


# Characters that pywinauto's keyboard parser treats as modifiers or commands.
_TYPE_KEYS_ESCAPE = set("+^%~(){}[]")


def _escape_for_type_keys(text: str) -> str:
    """Escape literal text for ``UIAWrapper.type_keys``.

    ``type_keys`` is the only injection path that reliably reaches WinUI3
    editors (Windows 11 Notepad): it focuses the real accessibility control and
    attaches to that control's input thread. Its mini-language treats
    ``+ ^ % ~ ( ) { } [ ]`` as commands, so literal occurrences must be wrapped
    in braces.
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(
        f"{{{char}}}" if char in _TYPE_KEYS_ESCAPE else char for char in normalized
    )



class InputController:
    def __init__(self, desktop: Any, emergency: Any) -> None:
        self.desktop = desktop
        self.emergency = emergency

    # -- guards ------------------------------------------------------------
    def _check(self) -> None:
        self.emergency.check()

    def _assert_safe_target(self) -> dict[str, Any]:
        active = self.desktop.active_window()
        title = str(active.get("title", "")).strip().lower()
        process = str(active.get("process", "")).strip().lower()
        if title in _SHELL_TITLES:
            raise ToolExecutionError(
                "Keyboard input into the Windows Run dialog is blocked.",
                hint="Computer use cannot be used as a shell shortcut. Use an explicitly approved shell tool instead.",
            )
        # Terminals are intentionally allowed (users may ask to type in one) but
        # the engine records the target so the transcript shows where input went.
        active["shell_target"] = process in _SHELL_PROCESSES
        return active

    def _active_text_control(self) -> Any | None:
        """Return the sole text control of the active window, or ``None``.

        Refusing to guess when several text controls exist prevents typing into
        the wrong field (for example the address bar instead of a page form).
        """
        try:
            from pywinauto import Desktop

            active = self.desktop.active_window()
            handle = active.get("handle")
            if not handle:
                return None
            window = Desktop(backend="uia").window(handle=handle)
            controls = []
            for role in ("Document", "Edit"):
                try:
                    controls.extend(window.descendants(control_type=role))
                except Exception:
                    continue
            if len(controls) != 1:
                return None
            return controls[0]
        except Exception as exc:
            log.info("Could not inspect the active window's text controls: %s", exc)
            return None

    def _focus_active_input_control(self) -> bool:
        """Give keyboard focus to the sole text control in the active window.

        WinUI3 applications such as Windows 11 Notepad route synthetic input
        through an ``InputSiteWindow`` and only accept it when the actual
        document control holds UI Automation focus.
        """
        control = self._active_text_control()
        if control is None:
            return False
        try:
            control.set_focus()
            # WinUI3 needs a moment to move its internal input site to the
            # control; characters injected too early are dropped or garbled.
            time.sleep(0.35)
            return True
        except Exception as exc:
            log.info("Could not focus the active text control: %s", exc)
            return False

    def _type_text_uia(self, text: str) -> bool:
        """Type through UI Automation: the reliable path for WinUI3 editors.

        ``pause=0.02`` is deliberate. Faster Unicode injection into Windows 11
        Notepad races the control's input handling and silently replaces
        characters (observed: ``04738C`` -> ``CCCCCC``). 50 characters/second is
        still fast enough for interactive use; longer text switches to an atomic
        clipboard paste in :meth:`type_text`.
        """
        control = self._active_text_control()
        if control is None:
            return False
        try:
            control.set_focus()
            time.sleep(0.25)
            control.type_keys(
                _escape_for_type_keys(text),
                with_spaces=True,
                with_tabs=True,
                with_newlines=True,
                pause=0.02,
                set_foreground=False,
            )
            return True
        except Exception as exc:
            log.info("UI Automation typing failed; falling back to SendInput: %s", exc)
            return False

    def _validate_point(self, x: int, y: int) -> None:
        try:
            import win32api

            left = win32api.GetSystemMetrics(76)
            top = win32api.GetSystemMetrics(77)
            width = win32api.GetSystemMetrics(78)
            height = win32api.GetSystemMetrics(79)
        except Exception as exc:
            raise ToolExecutionError(f"Could not determine the virtual screen bounds: {exc}") from exc
        if not (left <= x < left + width and top <= y < top + height):
            raise ToolExecutionError(
                f"Coordinates ({x},{y}) are outside the current screen bounds "
                f"({left},{top})-({left + width - 1},{top + height - 1})."
            )

    # -- mouse -------------------------------------------------------------
    def move(self, x: int, y: int) -> dict[str, Any]:
        self._check()
        self._validate_point(x, y)
        from pywinauto import mouse

        mouse.move(coords=(x, y))
        return {"x": x, "y": y}

    def click(
        self,
        x: int,
        y: int,
        *,
        button: str = "left",
        clicks: int = 1,
    ) -> dict[str, Any]:
        self._check()
        self._validate_point(x, y)
        if button not in {"left", "right", "middle"}:
            raise ToolExecutionError(f"Unsupported mouse button '{button}'.")
        if not (1 <= clicks <= 3):
            raise ToolExecutionError("Click count must be between 1 and 3.")
        from pywinauto import mouse

        before = self.desktop.active_window()
        if clicks == 1:
            mouse.click(button=button, coords=(x, y))
        elif clicks == 2:
            mouse.click(button=button, coords=(x, y), double=True)
        else:
            mouse.click(button=button, coords=(x, y))
            time.sleep(0.08)
            mouse.click(button=button, coords=(x, y))
            time.sleep(0.08)
            mouse.click(button=button, coords=(x, y))
        time.sleep(0.35)
        return {
            "x": x,
            "y": y,
            "button": button,
            "clicks": clicks,
            "active_before": before.get("title"),
            "active_after": self.desktop.active_window().get("title"),
        }

    def scroll(
        self,
        direction: str,
        amount: int,
        *,
        x: int | None = None,
        y: int | None = None,
    ) -> dict[str, Any]:
        self._check()
        if direction not in {"up", "down"}:
            raise ToolExecutionError("Scroll direction must be 'up' or 'down'.")
        if not (1 <= amount <= 50):
            raise ToolExecutionError("Scroll amount must be between 1 and 50 wheel steps.")
        if (x is None) != (y is None):
            raise ToolExecutionError("Provide both x and y, or neither.")
        if x is not None and y is not None:
            self._validate_point(x, y)
            self.move(x, y)
            point = (x, y)
        else:
            import win32api

            point = win32api.GetCursorPos()
        from pywinauto import mouse

        distance = amount * (1 if direction == "up" else -1)
        mouse.scroll(coords=point, wheel_dist=distance)
        return {"direction": direction, "amount": amount, "at": list(point)}

    def drag(
        self,
        from_x: int,
        from_y: int,
        to_x: int,
        to_y: int,
        *,
        button: str = "left",
        duration: float = 0.4,
    ) -> dict[str, Any]:
        self._check()
        self._validate_point(from_x, from_y)
        self._validate_point(to_x, to_y)
        if button not in {"left", "right"}:
            raise ToolExecutionError("Drag supports only the left or right mouse button.")
        from pywinauto import mouse

        before = self.desktop.active_window()
        mouse.move(coords=(from_x, from_y))
        time.sleep(0.1)
        mouse.press(button=button, coords=(from_x, from_y))
        try:
            steps = max(4, int(duration * 20))
            for step in range(1, steps + 1):
                self._check()
                x = int(from_x + (to_x - from_x) * step / steps)
                y = int(from_y + (to_y - from_y) * step / steps)
                mouse.move(coords=(x, y))
                time.sleep(max(0.01, duration / steps))
        finally:
            mouse.release(button=button, coords=(to_x, to_y))
        time.sleep(0.25)
        return {
            "from": [from_x, from_y],
            "to": [to_x, to_y],
            "button": button,
            "active_before": before.get("title"),
            "active_after": self.desktop.active_window().get("title"),
        }

    # -- keyboard ----------------------------------------------------------
    def _type_via_clipboard(self, text: str, active: dict[str, Any]) -> dict[str, Any]:
        """Paste long text atomically, restoring the previous clipboard."""
        try:
            saved = self.get_clipboard()
        except Exception:
            saved = None
        try:
            self.set_clipboard(text)
            data = self.paste()
            # The target reads the clipboard asynchronously; restoring earlier
            # would paste the old content instead.
            time.sleep(0.6)
        finally:
            if saved is not None:
                try:
                    self.set_clipboard(saved)
                except Exception as exc:
                    log.info("Could not restore the previous clipboard: %s", exc)
        data.update(
            {
                "characters": len(text),
                "method": "clipboard",
                "target_window": active.get("title"),
                "shell_target": active.get("shell_target", False),
            }
        )
        return data

    def type_text(self, text: str) -> dict[str, Any]:
        self._check()
        if not text:
            raise ToolExecutionError("Text is empty.")
        if len(text) > 10_000:
            raise ToolExecutionError("Text is too long for a single keyboard operation.")
        active = self._assert_safe_target()
        # A previous hotkey may have left Ctrl/Alt/Shift down (pywinauto can do
        # this on some layouts), which turns ordinary typing into shortcuts.
        _release_modifiers()
        if len(text) > 400:
            # Character-by-character injection of a long document is slow and
            # more likely to race; a single clipboard paste is atomic.
            return self._type_via_clipboard(text, active)

        method = "uia-type-keys"
        used_uia = self._type_text_uia(text)
        if not used_uia:
            method = "sendinput-unicode"
            self._focus_active_input_control()
            time.sleep(0.15)
            try:
                with _attached_to_foreground():
                    sent = _type_text_win32(text)
            finally:
                _release_modifiers()
        else:
            sent = len(text)
        data = {
            "characters": len(text),
            "sent_events": sent,
            "method": method,
            "active": self.desktop.active_window().get("title"),
        }
        data["target_window"] = active.get("title")
        data["shell_target"] = active.get("shell_target", False)
        return data

    def press_key(self, key: str) -> dict[str, Any]:
        self._check()
        raw = (key or "").strip()
        if not raw:
            raise ToolExecutionError("Key is empty.")
        lowered = raw.lower()
        if lowered in _BLOCKED_KEY_SEQUENCES:
            raise ToolExecutionError(
                f"Key sequence '{key}' is blocked because it opens a shell/elevation surface.",
                hint="Use an explicitly approved shell tool instead.",
            )
        active = self._assert_safe_target()
        _release_modifiers()

        # Accept legacy sequences such as "^s" or "ctrl+s" by delegating them to
        # the Win32 hotkey implementation instead of pywinauto's send_keys,
        # which mistyped literal letters for Ctrl+A/Ctrl+C on this machine.
        if "+" in raw or raw[0] in "^%+":
            keys: list[str] = []
            if raw[0] == "^":
                keys = ["ctrl", raw[1:]]
            elif raw[0] == "%":
                keys = ["alt", raw[1:]]
            elif raw[0] == "+":
                keys = ["shift", raw[1:]]
            else:
                keys = [part for part in raw.split("+") if part]
            data = self.hotkey(keys)
            data.update({"key": key, "method": "win32-hotkey"})
            return data

        vk = _resolve_vk(raw)
        if vk is None:
            raise ToolExecutionError(
                f"Unsupported key name '{key}'.",
                hint="Use a key name such as enter, tab, esc, home, end, f5, or a single character.",
            )
        _release_modifiers()
        self._focus_active_input_control()
        with _attached_to_foreground():
            _tap_key(vk)
        data = {"key": key, "vk": vk, "method": "win32", "active": self.desktop.active_window().get("title")}
        data["target_window"] = active.get("title")
        return data

    def hotkey(self, keys: list[str]) -> dict[str, Any]:
        self._check()
        normalized = [key.strip().lower() for key in keys if key.strip()]
        if not normalized:
            raise ToolExecutionError("No keys were provided.")
        if tuple(normalized) in _BLOCKED_HOTKEYS or "+".join(normalized) in _BLOCKED_KEY_SEQUENCES:
            raise ToolExecutionError(
                f"Hotkey {'+'.join(normalized)} is blocked because it opens a shell/elevation surface.",
                hint="Use an explicitly approved shell tool instead.",
            )
        if len(normalized) == 1:
            return self.press_key(normalized[0])

        *modifier_names, main_key = normalized
        modifier_vks: list[int] = []
        for name in modifier_names:
            if name not in {"ctrl", "control", "shift", "alt", "win", "lwin"}:
                raise ToolExecutionError(
                    f"Unsupported modifier '{name}'. Use ctrl, shift, alt or win.",
                    hint="A hotkey must be modifiers followed by one final key, e.g. ['ctrl', 'a'].",
                )
            modifier_vks.append(_VK["ctrl" if name in {"ctrl", "control"} else name])
        main_vk = _resolve_vk(main_key)
        if main_vk is None:
            raise ToolExecutionError(
                f"Unsupported hotkey key '{main_key}'.",
                hint="Use a single character or a key name such as enter, end, tab or f5.",
            )

        active = self._assert_safe_target()
        _release_modifiers()
        self._focus_active_input_control()
        try:
            with _attached_to_foreground():
                for vk in modifier_vks:
                    _keybd_event(vk)
                _tap_key(main_vk)
        finally:
            # Always release in reverse order; a stuck Ctrl turns every later
            # keystroke into an unintended shortcut.
            for vk in reversed(modifier_vks):
                try:
                    _keybd_event(vk, up=True)
                except Exception:
                    pass
        data = {
            "keys": normalized,
            "method": "win32",
            "active": self.desktop.active_window().get("title"),
            "target_window": active.get("title"),
        }
        return data

    def select_all(self) -> dict[str, Any]:
        return self.hotkey(["ctrl", "a"])

    def copy(self) -> dict[str, Any]:
        return self.hotkey(["ctrl", "c"])

    def paste(self) -> dict[str, Any]:
        return self.hotkey(["ctrl", "v"])

    def paste_text(self, text: str) -> dict[str, Any]:
        self._check()
        if len(text) > 200_000:
            raise ToolExecutionError("Clipboard text is too long.")
        self._assert_safe_target()
        self.set_clipboard(text)
        data = self.paste()
        # Clipboard paste is asynchronous in WinUI3; let the document settle so
        # the next action cannot race the insertion.
        time.sleep(0.4)
        data["characters"] = len(text)
        data["method"] = "clipboard"
        return data

    # -- clipboard ---------------------------------------------------------
    def set_clipboard(self, text: str) -> None:
        try:
            import win32clipboard
            import win32con
        except ImportError as exc:
            raise ToolExecutionError("pywin32 is required for clipboard access.") from exc
        last_error: Exception | None = None
        for _ in range(6):
            try:
                win32clipboard.OpenClipboard()
                try:
                    win32clipboard.EmptyClipboard()
                    win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
                finally:
                    win32clipboard.CloseClipboard()
                return
            except Exception as exc:  # clipboard is a shared OS resource
                last_error = exc
                time.sleep(0.08)
        raise ToolExecutionError(f"Could not write to the clipboard: {last_error}")

    def get_clipboard(self) -> str:
        try:
            import win32clipboard
            import win32con
        except ImportError as exc:
            raise ToolExecutionError("pywin32 is required for clipboard access.") from exc
        last_error: Exception | None = None
        for _ in range(6):
            try:
                win32clipboard.OpenClipboard()
                try:
                    if not win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                        return ""
                    value = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
                    return str(value or "")
                finally:
                    win32clipboard.CloseClipboard()
            except Exception as exc:
                last_error = exc
                time.sleep(0.08)
        raise ToolExecutionError(f"Could not read the clipboard: {last_error}")
