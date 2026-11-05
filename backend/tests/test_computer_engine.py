"""Computer-use engine tests with controlled fakes (no real mouse/keyboard)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.computer.engine import ComputerUseEngine
from app.core.enums import ToolPermission
from app.core.errors import EmergencyStopError, ToolExecutionError
from app.core.events import EventBus
from app.core.config import Settings
from app.permissions.engine import EmergencyState


class FakeDesktop:
    def __init__(self) -> None:
        self.settings = SimpleNamespace(tesseract_cmd="")
        self.typed: list[str] = []
        self.screenshot_calls = 0
        self.fail_screenshot = False

    def active_window(self):
        return {
            "handle": 1,
            "title": "Untitled - Notepad",
            "class_name": "Notepad",
            "pid": 10,
            "process": "notepad.exe",
            "rect": {"left": 0, "top": 0, "right": 800, "bottom": 600},
        }

    def list_windows(self):
        return [self.active_window()]

    def screenshot(self, monitor: int = 0):
        self.screenshot_calls += 1
        if self.fail_screenshot:
            raise ToolExecutionError("Screenshot device unavailable.")
        return {
            "path": "C:/tmp/fake.png",
            "url": "/api/live/screenshots/fake.png",
            "size": 123,
            "monitor": monitor,
            "monitors": 1,
        }

    def get_screen_text(self, monitor: int = 0):
        raise ToolExecutionError("Tesseract OCR is not installed.")

    def open_application(self, app_name: str):
        return {"launched": app_name, "method": "fake"}

    def focus_window(self, window_name: str):
        return {"requested": window_name, "active": self.active_window(), "focused": True}

    def type_text(self, text: str):
        self.typed.append(text)
        return {"characters": len(text), "active": self.active_window()["title"]}

    def press_key(self, key: str):
        return {"key": key, "active": self.active_window()["title"]}

    def hotkey(self, keys):
        return {"keys": keys, "sequence": "^", "active": self.active_window()["title"]}

    def scroll(self, direction: str, amount: int):
        return {"direction": direction, "amount": amount, "at": [0, 0]}

    def close_application(self, app_name: str):
        return {"terminated": [], "still_running": []}


@pytest.fixture
def computer_engine(tmp_path):
    desktop = FakeDesktop()
    emergency = EmergencyState()
    settings = Settings(
        environment="test",
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        llm_provider="mock",
        permission_mode="assisted",
    )
    services = SimpleNamespace(
        desktop=desktop,
        emergency=emergency,
        settings=settings,
        events=EventBus(),
    )
    engine = ComputerUseEngine(services)
    engine.observer.elements = lambda **kwargs: {
        "elements": [
            {
                "label": "Save",
                "role": "Button",
                "rect": {"left": 10, "top": 10, "right": 60, "bottom": 30},
                "source": "uia",
            }
        ],
        "source": "uia",
        "available": True,
    }
    engine.observer.ocr_available = lambda: False
    engine.observer.capabilities = lambda: {
        "screenshot": True,
        "uia": True,
        "win32": True,
        "ocr": False,
        "clipboard": True,
        "input": True,
    }
    engine.observer.screens = lambda: {
        "monitors": [{"left": 0, "top": 0, "width": 1920, "height": 1080}],
        "primary": 1,
        "count": 1,
        "source": "fake",
    }
    engine.input.click = lambda *args, **kwargs: {
        "x": 35,
        "y": 20,
        "button": "left",
        "clicks": 1,
        "active_before": "Untitled - Notepad",
        "active_after": "Untitled - Notepad",
    }
    engine.input.move = lambda x, y: {"x": x, "y": y}
    engine.input.scroll = lambda direction, amount, x=None, y=None: {
        "direction": direction,
        "amount": amount,
        "at": [x or 0, y or 0],
    }
    engine.input.drag = lambda *args, **kwargs: {
        "from": [0, 0],
        "to": [1, 1],
        "active_before": "Notepad",
        "active_after": "Notepad",
    }
    engine.input.type_text = lambda text: desktop.type_text(text)
    engine.input.press_key = lambda key: desktop.press_key(key)
    engine.input.hotkey = lambda keys: desktop.hotkey(keys)
    engine.input.get_clipboard = lambda: "Hello Mochi"
    return engine


async def test_observation_is_structured_and_honest_about_ocr(computer_engine):
    observation = await computer_engine.observe(include_ocr=True, include_screenshot=True)
    assert observation["untrusted"] is True
    assert observation["active_window"]["title"] == "Untitled - Notepad"
    assert observation["screen"]["width"] == 1920
    assert observation["element_count"] == 1
    assert observation["ocr"] is None
    assert any("OCR is unavailable" in warning for warning in observation["warnings"])
    assert "uia" in observation["detection_sources"]


async def test_observation_survives_screenshot_failure(computer_engine):
    computer_engine.services.desktop.fail_screenshot = True
    observation = await computer_engine.observe(include_elements=False)
    assert observation["screenshot"] is None
    assert any("Screenshot failed" in warning for warning in observation["warnings"])


async def test_missing_element_fails_instead_of_inventing_it(computer_engine):
    computer_engine.observer.find_element = lambda *args, **kwargs: None
    with pytest.raises(ToolExecutionError) as excinfo:
        await computer_engine.click_element("Send")
    assert "not found" in str(excinfo.value).lower()


async def test_consequential_element_requires_approval_tool(computer_engine):
    computer_engine.observer.find_element = lambda *args, **kwargs: {
        "label": "Send message",
        "role": "Button",
        "rect": {"left": 10, "top": 10, "right": 60, "bottom": 30},
    }
    with pytest.raises(ToolExecutionError) as excinfo:
        await computer_engine.click_element("Send message")
    assert "consequential" in str(excinfo.value).lower()
    assert "consequential_click" in (excinfo.value.hint or "")


async def test_approved_consequential_click_executes(computer_engine):
    computer_engine.observer.find_element = lambda *args, **kwargs: {
        "label": "Send message",
        "role": "Button",
        "rect": {"left": 10, "top": 10, "right": 60, "bottom": 30},
    }
    result = await computer_engine.click_element("Send message", approved=True)
    assert result["guard"]["approved"] is True
    assert result["verification"]["verified"] is False  # no window change in fake


async def test_text_typing_is_verified_from_uia(computer_engine):
    from app.computer import verification

    original = verification._uia_text_for_window
    verification._uia_text_for_window = lambda observer, title, *args, **kwargs: [("document", "Hello Mochi")]
    try:
        result = await computer_engine.type_text("Hello Mochi")
    finally:
        verification._uia_text_for_window = original
    assert result["verification"]["verified"] is True
    assert result["verification"]["method"] == "uia"
    assert computer_engine.services.desktop.typed == ["Hello Mochi"]


async def test_text_typing_reports_when_verification_is_unavailable(computer_engine):
    from app.computer import verification

    original = verification._uia_text_for_window
    verification._uia_text_for_window = lambda observer, title, *args, **kwargs: []
    try:
        result = await computer_engine.type_text("Hello Mochi")
    finally:
        verification._uia_text_for_window = original
    assert result["verification"]["verified"] is False
    assert "could not be verified" in result["verification"]["detail"]


async def test_emergency_stop_during_workflow_blocks_next_input(computer_engine):
    await computer_engine.observe(include_elements=False)
    computer_engine.services.emergency.engage("test stop")
    with pytest.raises(EmergencyStopError):
        await computer_engine.type_text("should not be typed")
    assert computer_engine.services.desktop.typed == []
    state = await computer_engine.state()
    assert state["emergency"]["stopped"] is True


async def test_invalid_coordinates_are_rejected_before_input(computer_engine):
    real_input = __import__("app.computer.input", fromlist=["InputController"]).InputController(
        computer_engine.services.desktop, computer_engine.services.emergency
    )
    with pytest.raises(ToolExecutionError):
        real_input.click(999_999, 999_999)
    with pytest.raises(ToolExecutionError):
        real_input.move(-5, 10)


def test_hotkey_uses_win32_events_and_releases_modifiers(monkeypatch, computer_engine):
    from app.computer import input as input_module

    events: list[tuple[object, bool]] = []
    monkeypatch.setattr(
        input_module, "_keybd_event", lambda vk, up=False: events.append((vk, up))
    )
    monkeypatch.setattr(
        input_module, "_release_modifiers", lambda: events.append(("release", True))
    )
    controller = input_module.InputController(
        computer_engine.services.desktop, computer_engine.services.emergency
    )
    result = controller.hotkey(["ctrl", "a"])

    # Modifiers are cleared first, pressed in order, tapped, released in reverse.
    assert events == [
        ("release", True),
        (0x11, False),
        (0x41, False),
        (0x41, True),
        (0x11, True),
    ]
    assert result["keys"] == ["ctrl", "a"]
    assert result["method"] == "win32"


def test_press_key_supports_named_keys_and_legacy_sequences(monkeypatch, computer_engine):
    from app.computer import input as input_module

    events: list[tuple[object, bool]] = []
    monkeypatch.setattr(
        input_module, "_keybd_event", lambda vk, up=False: events.append((vk, up))
    )
    monkeypatch.setattr(input_module, "_release_modifiers", lambda: None)
    controller = input_module.InputController(
        computer_engine.services.desktop, computer_engine.services.emergency
    )

    controller.press_key("enter")
    assert events == [(0x0D, False), (0x0D, True)]

    events.clear()
    legacy = controller.press_key("^s")
    assert events == [(0x11, False), (0x53, False), (0x53, True), (0x11, True)]
    assert legacy["method"] == "win32-hotkey"


def test_unicode_typing_uses_sendinput_and_handles_newlines(monkeypatch):
    from app.computer import input as input_module

    codepoints: list[int] = []
    taps: list[int] = []
    monkeypatch.setattr(input_module, "_send_unicode", codepoints.append)
    monkeypatch.setattr(input_module, "_tap_key", taps.append)

    sent = input_module._type_text_win32("A\r\nB\n\té😀")
    assert codepoints[:2] == [ord("A"), ord("B")]
    assert codepoints[-3:] == [ord("é"), 0xD83D, 0xDE00]
    assert taps == [0x0D, 0x0D, 0x09]
    assert sent == len(codepoints) + len(taps)


def test_shell_launch_hotkeys_are_blocked(monkeypatch, computer_engine):
    from app.computer import input as input_module

    monkeypatch.setattr(input_module, "_keybd_event", lambda *args, **kwargs: None)
    controller = input_module.InputController(
        computer_engine.services.desktop, computer_engine.services.emergency
    )
    for keys in (["win", "r"], ["win", "x"], ["ctrl", "shift", "enter"]):
        with pytest.raises(ToolExecutionError):
            controller.hotkey(keys)


def test_release_modifiers_only_releases_keys_that_are_down(monkeypatch):
    """Stray key-up events make WinUI3 editors drop the next character."""
    from app.computer import input as input_module
    import win32api

    down = {0x11}
    monkeypatch.setattr(
        win32api, "GetAsyncKeyState", lambda vk: 0x8000 if vk in down else 0
    )
    events: list[tuple[int, bool]] = []
    monkeypatch.setattr(
        input_module, "_keybd_event", lambda vk, up=False: events.append((vk, up))
    )
    monkeypatch.setattr(input_module.time, "sleep", lambda _seconds: None)

    input_module._release_modifiers()
    assert events == [(0x11, True)]


def test_type_keys_escaping_preserves_literal_text():
    from app.computer import input as input_module

    assert input_module._escape_for_type_keys("a+b^c%d~e(f)g{h}i[j]") == (
        "a{+}b{^}c{%}d{~}e{(}f{)}g{{}h{}}i{[}j{]}"
    )
    assert input_module._escape_for_type_keys("one\r\ntwo\rthree") == "one\ntwo\nthree"


def test_typing_prefers_uia_and_reports_the_method(monkeypatch, computer_engine):
    from app.computer import input as input_module

    class FakeControl:
        def __init__(self) -> None:
            self.focused = False
            self.sent: list[tuple[str, dict]] = []

        def set_focus(self) -> None:
            self.focused = True

        def type_keys(self, keys: str, **kwargs) -> None:
            self.sent.append((keys, kwargs))

    control = FakeControl()
    monkeypatch.setattr(input_module, "_release_modifiers", lambda: None)
    controller = input_module.InputController(
        computer_engine.services.desktop, computer_engine.services.emergency
    )
    monkeypatch.setattr(controller, "_active_text_control", lambda: control)

    result = controller.type_text("Hi+[you]")
    assert result["method"] == "uia-type-keys"
    assert control.focused is True
    assert control.sent[0][0] == "Hi{+}{[}you{]}"
    assert control.sent[0][1]["pause"] >= 0.02


def test_typing_falls_back_to_sendinput_without_a_single_text_control(
    monkeypatch, computer_engine
):
    import contextlib
    from app.computer import input as input_module

    monkeypatch.setattr(input_module, "_release_modifiers", lambda: None)
    monkeypatch.setattr(input_module, "_attached_to_foreground", contextlib.nullcontext)
    captured: list[str] = []
    monkeypatch.setattr(
        input_module, "_type_text_win32", lambda text: captured.append(text) or len(text)
    )
    controller = input_module.InputController(
        computer_engine.services.desktop, computer_engine.services.emergency
    )
    monkeypatch.setattr(controller, "_active_text_control", lambda: None)
    monkeypatch.setattr(controller, "_focus_active_input_control", lambda: True)

    result = controller.type_text("fallback")
    assert result["method"] == "sendinput-unicode"
    assert captured == ["fallback"]


def test_verify_text_uses_the_stable_window_handle(monkeypatch):
    from app.computer import verification

    class Observer:
        def capabilities(self):
            return {"uia": True, "ocr": False}

    seen: dict[str, int | None] = {}

    def fake_uia(observer, title, handle):
        seen["handle"] = handle
        return [("document", "hello world")]

    monkeypatch.setattr(verification, "_uia_text_for_window", fake_uia)
    result = verification.verify_text(
        Observer(), "hello", window_title=None, window_handle=4242, attempts=1
    )
    assert result["verified"] is True
    assert seen["handle"] == 4242


async def test_engine_state_reports_activity_and_limitations(computer_engine):
    await computer_engine.observe(include_elements=False)
    state = await computer_engine.state()
    assert state["engine"] == "windows-uia"
    assert state["activity"]["status"] == "completed"
    assert state["last_observation"] is not None
    assert state["capabilities"]["ocr"] is False


async def test_computer_tools_have_expected_permissions(services):
    type_tool = services.registry.get("computer.type_text")
    assert type_tool is not None and type_tool.permission == ToolPermission.WRITE

    consequential = services.registry.get("computer.consequential_click")
    assert consequential is not None
    assert consequential.requires_approval is True
    decision = await services.permissions.evaluate(consequential)
    assert decision.allowed is True and decision.requires_approval is True

    observe = services.registry.get("computer.observe")
    assert observe is not None and observe.permission == ToolPermission.READ

    clipboard = services.registry.get("computer.read_clipboard")
    assert clipboard is not None and clipboard.requires_approval is True

    services.emergency.engage("permission test")
    try:
        blocked = await services.permissions.evaluate(type_tool)
        assert blocked.allowed is False
    finally:
        services.emergency.clear()


async def test_observation_cancellation_does_not_swallow_cancel(computer_engine):
    import time as _time

    computer_engine.observer.observe = lambda **kwargs: (_time.sleep(0.4), {"timestamp": "x"})[1]
    task = asyncio.create_task(computer_engine.observe(include_elements=False))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
