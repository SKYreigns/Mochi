"""Windows computer-use engine: observe → act → observe again → verify."""

from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timezone
from typing import Any, Callable

from app.computer.input import InputController
from app.computer.observer import DesktopObserver
from app.computer.verification import (
    verify_active_window,
    verify_clipboard,
    verify_element,
    verify_text,
)
from app.core.errors import ToolExecutionError
from app.core.logging import get_logger, redact_value

log = get_logger("computer.engine")

_CONSEQUENTIAL_LABELS = re.compile(
    r"\b(send|submit|purchase|buy|pay|checkout|order|delete|remove|uninstall|install|"
    r"share|publish|post|confirm|allow|accept|agree|transfer|withdraw|format|"
    r"run as administrator|elevate|sign out|log out)\b",
    re.IGNORECASE,
)

_APP_TOKENS = {
    "chrome": "chrome",
    "google chrome": "chrome",
    "edge": "msedge",
    "microsoft edge": "msedge",
    "vscode": "code",
    "vs code": "code",
    "notepad": "notepad",
    "calculator": "calc",
    "paint": "mspaint",
    "explorer": "explorer",
    "file explorer": "explorer",
    "word": "winword",
    "excel": "excel",
    "powerpoint": "powerpnt",
    "terminal": "wt",
}


class ComputerUseEngine:
    def __init__(self, services: Any) -> None:
        self.services = services
        self.observer = DesktopObserver(services.desktop)
        self.input = InputController(services.desktop, services.emergency)
        self._activity: dict[str, Any] = {
            "status": "idle",
            "action": None,
            "task_id": None,
            "conversation_id": None,
            "started_at": None,
            "finished_at": None,
            "detail": "",
        }
        self._last_observation: dict[str, Any] | None = None
        self._last_verification: dict[str, Any] | None = None
        self._last_error: str | None = None

    # -- activity/state ----------------------------------------------------
    def _begin(self, action: str, *, task_id: str | None, conversation_id: str | None) -> None:
        self._activity = {
            "status": "stopped" if self.services.emergency.stopped else "executing",
            "action": action,
            "task_id": task_id,
            "conversation_id": conversation_id,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "detail": action.replace("_", " "),
        }

    def _finish(self, status: str, *, detail: str = "", error: str | None = None) -> None:
        self._activity["status"] = status
        self._activity["detail"] = detail or self._activity.get("detail", "")
        self._activity["finished_at"] = datetime.now(timezone.utc).isoformat()
        if error:
            self._last_error = error

    def _check_action(self) -> None:
        self.services.emergency.check()

    async def _publish(self, event: str, payload: dict[str, Any]) -> None:
        try:
            await self.services.events.publish(event, redact_value(payload))
        except Exception as exc:  # event fan-out must never break an action
            log.info("Could not publish %s: %s", event, exc)

    async def state(self) -> dict[str, Any]:
        active: dict[str, Any] = {}
        try:
            active = self.observer.active_window()
        except Exception as exc:
            active = {"error": str(exc)}
        try:
            screen = self.observer.screens()
        except Exception:
            screen = {"monitors": [], "count": 0, "primary": 1}
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "engine": "windows-uia",
            "capabilities": self.observer.capabilities(),
            "permission_mode": self.services.settings.permission_mode,
            "emergency": self.services.emergency.snapshot(),
            "active_window": active,
            "screen": screen,
            "activity": dict(self._activity),
            "last_observation": self._last_observation,
            "last_verification": self._last_verification,
            "last_error": self._last_error,
        }

    def _summarize_observation(self, observation: dict[str, Any]) -> dict[str, Any]:
        return {
            "timestamp": observation.get("timestamp"),
            "active_window": (observation.get("active_window") or {}).get("title"),
            "active_monitor": observation.get("active_monitor"),
            "window_count": observation.get("window_count", 0),
            "element_count": observation.get("element_count", 0),
            "detection_sources": observation.get("detection_sources", []),
            "screenshot": (observation.get("screenshot") or {}).get("url"),
            "warnings": observation.get("warnings", []),
            "duration_ms": observation.get("duration_ms"),
        }

    async def _call(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        return await asyncio.to_thread(fn, *args, **kwargs)

    # -- observation -------------------------------------------------------
    async def observe(
        self,
        *,
        include_elements: bool = True,
        include_ocr: bool = False,
        include_screenshot: bool = True,
        window_title: str | None = None,
        element_limit: int = 60,
        task_id: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        self._begin("observe", task_id=task_id, conversation_id=conversation_id)
        self._activity["status"] = "observing"
        try:
            data = await self._call(
                self.observer.observe,
                include_elements=include_elements,
                include_ocr=include_ocr,
                include_screenshot=include_screenshot,
                window_title=window_title,
                element_limit=element_limit,
            )
        except Exception as exc:
            self._finish("failed", detail="Observation failed", error=str(exc))
            await self._publish(
                "computer.verification",
                {
                    "task_id": task_id,
                    "conversation_id": conversation_id,
                    "status": "failed",
                    "verified": False,
                    "detail": f"Observation failed: {exc}",
                },
            )
            raise
        self._last_observation = self._summarize_observation(data)
        self._last_verification = {
            "verified": True,
            "method": "observation",
            "detail": "Desktop observation captured.",
            "evidence": f"{data.get('window_count', 0)} windows, {data.get('element_count', 0)} UI elements",
            "timestamp": data.get("timestamp"),
        }
        self._finish("completed", detail=f"Observed {data.get('window_count', 0)} windows")
        await self._publish(
            "computer.observation",
            {
                "task_id": task_id,
                "conversation_id": conversation_id,
                "status": "completed",
                **self._last_observation,
            },
        )
        return data

    async def screenshot(self, monitor: int = 0, *, task_id: str | None = None) -> dict[str, Any]:
        self._begin("screenshot", task_id=task_id, conversation_id=None)
        try:
            data = await self._call(self.observer.screenshot, monitor)
        except Exception as exc:
            self._finish("failed", detail="Screenshot failed", error=str(exc))
            raise
        self._finish("completed", detail=f"Captured {data.get('size', 0)} bytes")
        await self._publish(
            "computer.screenshot",
            {"task_id": task_id, "status": "completed", **data},
        )
        return data

    # -- application/window control ---------------------------------------
    async def wait_for_window(
        self,
        app_name: str,
        *,
        timeout: float = 8.0,
        interval: float = 0.25,
    ) -> dict[str, Any] | None:
        needle = _APP_TOKENS.get(app_name.strip().lower(), app_name.strip().lower())
        needle = needle.replace(".exe", "")
        deadline = time.monotonic() + max(0.5, timeout)
        while time.monotonic() < deadline:
            try:
                windows = await self._call(self.observer.list_windows)
            except Exception:
                windows = []
            for window in windows:
                title = str(window.get("title", "")).lower()
                if needle and needle in title:
                    return window
                pid = window.get("pid")
                if pid:
                    try:
                        import psutil

                        process_name = psutil.Process(int(pid)).name().lower().replace(".exe", "")
                        if needle and needle in process_name:
                            return window
                    except Exception:
                        continue
            await asyncio.sleep(interval)
        return None

    async def open_application(
        self,
        app_name: str,
        *,
        task_id: str | None = None,
        conversation_id: str | None = None,
        timeout: float = 8.0,
    ) -> dict[str, Any]:
        self._check_action()
        self._begin("open_application", task_id=task_id, conversation_id=conversation_id)
        try:
            launched = await self._call(self.services.desktop.open_application, app_name)
        except Exception as exc:
            self._finish("failed", detail=f"Launch failed for {app_name}", error=str(exc))
            raise
        self._activity["status"] = "verifying"
        window = await self.wait_for_window(app_name, timeout=timeout)
        verification: dict[str, Any]
        if window is not None:
            # Bring the launched window to the foreground when possible and
            # verify the active title from a fresh read.
            await self._call(self.services.desktop.focus_window, window["title"][:120])
            verification = verify_active_window(self.observer, window["title"][:60], attempts=4, interval=0.15)
        else:
            verification = {
                "verified": False,
                "method": "window",
                "detail": f"{app_name!r} was launched but no matching window appeared within {timeout:.0f}s.",
                "evidence": "",
            }
        self._last_verification = {**verification, "timestamp": datetime.now(timezone.utc).isoformat()}
        status = "completed" if verification.get("verified") else "failed"
        self._finish(
            status,
            detail=verification.get("detail", ""),
            error=None if verification.get("verified") else verification.get("detail"),
        )
        await self._publish(
            "computer.action",
            {
                "task_id": task_id,
                "conversation_id": conversation_id,
                "action": "open_application",
                "status": status,
                "verified": bool(verification.get("verified")),
                "detail": verification.get("detail"),
                "window": window,
            },
        )
        return {"launched": launched, "window": window, "verification": verification}

    async def focus_window(
        self,
        window_name: str,
        *,
        task_id: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        self._check_action()
        self._begin("focus_window", task_id=task_id, conversation_id=conversation_id)
        try:
            data = await self._call(self.services.desktop.focus_window, window_name)
        except Exception as exc:
            self._finish("failed", detail=f"Could not focus {window_name!r}", error=str(exc))
            raise
        self._activity["status"] = "verifying"
        verification = verify_active_window(self.observer, window_name, attempts=6, interval=0.15)
        self._last_verification = {**verification, "timestamp": datetime.now(timezone.utc).isoformat()}
        status = "completed" if verification.get("verified") else "failed"
        self._finish(status, detail=verification.get("detail", ""))
        await self._publish(
            "computer.action",
            {
                "task_id": task_id,
                "conversation_id": conversation_id,
                "action": "focus_window",
                "status": status,
                "verified": bool(verification.get("verified")),
                "detail": verification.get("detail"),
            },
        )
        return {"focus": data, "verification": verification}

    # -- mouse actions -----------------------------------------------------
    async def _screen_for_consequential(self, label: str | None, *, approved: bool) -> dict[str, Any] | None:
        if not label:
            return None
        if not _CONSEQUENTIAL_LABELS.search(label):
            return None
        if approved:
            return {"label": label, "consequential": True, "approved": True}
        raise ToolExecutionError(
            f"Element {label!r} looks consequential (send/submit/purchase/delete/install/security).",
            hint=(
                "Use computer.consequential_click with an explicit user approval, or ask the user "
                "to perform this step manually."
            ),
        )

    async def click(
        self,
        x: int,
        y: int,
        *,
        button: str = "left",
        clicks: int = 1,
        task_id: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        self._check_action()
        element = await self._call(self.observer.element_at, x, y)
        guard = await self._screen_for_consequential(
            (element or {}).get("label") if element else None,
            approved=False,
        )
        self._begin("click", task_id=task_id, conversation_id=conversation_id)
        try:
            data = await self._call(self.input.click, x, y, button=button, clicks=clicks)
        except Exception as exc:
            self._finish("failed", detail=f"Click failed at ({x},{y})", error=str(exc))
            raise
        verification = {
            "verified": False,
            "method": "observation",
            "detail": "The click was injected; its outcome is not independently verified. Observe the window before claiming success.",
            "evidence": f"Active window after click: {data.get('active_after')!r}",
        }
        self._last_verification = {**verification, "timestamp": datetime.now(timezone.utc).isoformat()}
        self._finish("completed", detail=verification["detail"])
        await self._publish(
            "computer.action",
            {
                "task_id": task_id,
                "conversation_id": conversation_id,
                "action": "click",
                "status": "completed",
                "verified": False,
                "detail": verification["detail"],
                "element": element,
                "guard": guard,
            },
        )
        return {"click": data, "element": element, "verification": verification}

    async def click_element(
        self,
        label: str,
        *,
        window_name: str | None = None,
        role: str | None = None,
        button: str = "left",
        clicks: int = 1,
        approved: bool = False,
        task_id: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        self._check_action()
        element = await self._call(
            self.observer.find_element, label, window_title=window_name, role=role
        )
        if element is None:
            self._last_error = f"Element {label!r} not found."
            await self._publish(
                "computer.action",
                {
                    "task_id": task_id,
                    "conversation_id": conversation_id,
                    "action": "click_element",
                    "status": "failed",
                    "verified": False,
                    "detail": self._last_error,
                },
            )
            raise ToolExecutionError(
                f"Element {label!r} was not found in the current UI Automation tree.",
                hint="Call computer.observe first and click only elements that were observed.",
            )
        guard = await self._screen_for_consequential(label, approved=approved)
        rect = element.get("rect") or {}
        try:
            x = (int(rect["left"]) + int(rect["right"])) // 2
            y = (int(rect["top"]) + int(rect["bottom"])) // 2
        except (KeyError, TypeError, ValueError) as exc:
            raise ToolExecutionError(f"Element {label!r} has no usable bounding rectangle.") from exc

        self._begin("click_element", task_id=task_id, conversation_id=conversation_id)
        before = await self._call(self.observer.active_window)
        try:
            data = await self._call(self.input.click, x, y, button=button, clicks=clicks)
        except Exception as exc:
            self._finish("failed", detail=f"Click on {label!r} failed", error=str(exc))
            raise
        self._activity["status"] = "verifying"
        present = verify_element(self.observer, label, window_title=window_name)
        after_title = str((data or {}).get("active_after") or "")
        changed = bool(after_title) and after_title != str(before.get("title") or "")
        verification = {
            "verified": bool(changed),
            "method": "uia+window",
            "detail": (
                f"Clicked {label!r}; the active window changed to {after_title!r}."
                if changed
                else f"Clicked {label!r}; no active-window change was observed, so the outcome is unconfirmed."
            ),
            "evidence": (
                f"Element still present: {bool(present.get('verified'))}; active window after: {after_title!r}"
            ),
            "element": element,
        }
        self._last_verification = {**verification, "timestamp": datetime.now(timezone.utc).isoformat()}
        self._finish("completed", detail=verification["detail"])
        await self._publish(
            "computer.action",
            {
                "task_id": task_id,
                "conversation_id": conversation_id,
                "action": "consequential_click" if approved else "click_element",
                "status": "completed",
                "verified": verification["verified"],
                "detail": verification["detail"],
                "guard": guard,
            },
        )
        return {"click": data, "element": element, "verification": verification, "guard": guard}

    async def move_pointer(
        self,
        x: int,
        y: int,
        *,
        task_id: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        self._check_action()
        self._begin("move_pointer", task_id=task_id, conversation_id=conversation_id)
        try:
            data = await self._call(self.input.move, x, y)
        except Exception as exc:
            self._finish("failed", detail=f"Pointer move failed at ({x},{y})", error=str(exc))
            raise
        self._finish("completed", detail=f"Moved pointer to ({x},{y})")
        await self._publish(
            "computer.action",
            {
                "task_id": task_id,
                "conversation_id": conversation_id,
                "action": "move_pointer",
                "status": "completed",
                "verified": False,
                "detail": f"Pointer moved to ({x},{y}).",
            },
        )
        return {"move": data, "verification": {"verified": False, "detail": "Pointer position is not independently verified."}}

    async def scroll(
        self,
        direction: str,
        amount: int = 3,
        *,
        x: int | None = None,
        y: int | None = None,
        task_id: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        self._check_action()
        self._begin("scroll", task_id=task_id, conversation_id=conversation_id)
        try:
            data = await self._call(self.input.scroll, direction, amount, x=x, y=y)
        except Exception as exc:
            self._finish("failed", detail="Scroll failed", error=str(exc))
            raise
        self._finish("completed", detail=f"Scrolled {direction} {amount} steps")
        await self._publish(
            "computer.action",
            {
                "task_id": task_id,
                "conversation_id": conversation_id,
                "action": "scroll",
                "status": "completed",
                "verified": False,
                "detail": f"Scrolled {direction} {amount} steps at {data.get('at')}.",
            },
        )
        return {"scroll": data, "verification": {"verified": False, "detail": "Scroll impact is not independently verified without OCR/UI Automation evidence."}}

    async def drag(
        self,
        from_x: int,
        from_y: int,
        to_x: int,
        to_y: int,
        *,
        button: str = "left",
        task_id: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        self._check_action()
        self._begin("drag", task_id=task_id, conversation_id=conversation_id)
        try:
            data = await self._call(
                self.input.drag, from_x, from_y, to_x, to_y, button=button
            )
        except Exception as exc:
            self._finish("failed", detail="Drag failed", error=str(exc))
            raise
        verification = {
            "verified": False,
            "method": "observation",
            "detail": "Drag was injected; the drop result is not independently verified.",
            "evidence": f"Active window after drag: {data.get('active_after')!r}",
        }
        self._last_verification = {**verification, "timestamp": datetime.now(timezone.utc).isoformat()}
        self._finish("completed", detail=verification["detail"])
        await self._publish(
            "computer.action",
            {
                "task_id": task_id,
                "conversation_id": conversation_id,
                "action": "drag",
                "status": "completed",
                "verified": False,
                "detail": verification["detail"],
            },
        )
        return {"drag": data, "verification": verification}

    async def _ensure_target_window(
        self,
        window_name: str | None,
        window_handle: int | None = None,
    ) -> dict[str, Any] | None:
        """Refuse to send input unless the intended window is verifiably active.

        A handle is preferred when the caller has one: window titles change as
        soon as a document becomes dirty (``*Untitled - Notepad``), while the
        handle stays stable for the life of the window.
        """
        if not window_name and not window_handle:
            return None
        needle = (window_name or "").strip().lower()
        active = await self._call(self.observer.active_window)
        if window_handle and int(active.get("handle") or 0) == int(window_handle):
            return active
        if needle and needle in str(active.get("title", "")).lower():
            return active
        try:
            await self._call(
                self.services.desktop.focus_window,
                window_name or str(active.get("title") or ""),
            )
        except Exception:
            pass
        active = await self._call(self.observer.active_window)
        if window_handle and int(active.get("handle") or 0) == int(window_handle):
            return active
        if needle and needle in str(active.get("title", "")).lower():
            return active
        raise ToolExecutionError(
            f"Refusing to send input: active window {active.get('title')!r} does not match {window_name!r}.",
            hint="Focus the intended window and observe it first; never type into an unverified window.",
        )

    # -- keyboard actions --------------------------------------------------
    async def type_text(
        self,
        text: str,
        *,
        window_name: str | None = None,
        window_handle: int | None = None,
        verify: bool = True,
        task_id: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        self._check_action()
        target = await self._ensure_target_window(window_name, window_handle)
        self._begin("type_text", task_id=task_id, conversation_id=conversation_id)
        try:
            data = await self._call(self.input.type_text, text)
        except Exception as exc:
            self._finish("failed", detail="Typing failed", error=str(exc))
            raise
        if target:
            data["target_window"] = target.get("title")
            data["target_handle"] = target.get("handle")
        self._activity["status"] = "verifying"
        if verify:
            verification = await self._call(
                verify_text,
                self.observer,
                text,
                window_title=window_name,
                window_handle=(target or {}).get("handle"),
            )
        else:
            verification = {
                "verified": False,
                "method": "none",
                "detail": "Typing was requested without verification.",
                "evidence": "",
            }
        self._last_verification = {**verification, "timestamp": datetime.now(timezone.utc).isoformat()}
        self._finish("completed", detail=verification.get("detail", ""))
        await self._publish(
            "computer.verification",
            {
                "task_id": task_id,
                "conversation_id": conversation_id,
                "action": "type_text",
                "status": "completed",
                "verified": bool(verification.get("verified")),
                "detail": verification.get("detail"),
                "evidence": verification.get("evidence"),
            },
        )
        return {"input": data, "verification": verification}

    async def press_key(
        self,
        key: str,
        *,
        task_id: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        self._check_action()
        self._begin("press_key", task_id=task_id, conversation_id=conversation_id)
        try:
            data = await self._call(self.input.press_key, key)
        except Exception as exc:
            self._finish("failed", detail=f"Key press failed: {key}", error=str(exc))
            raise
        verification = {
            "verified": False,
            "method": "none",
            "detail": f"Key {key!r} was injected; its effect must be verified with a fresh observation.",
        }
        self._last_verification = {**verification, "timestamp": datetime.now(timezone.utc).isoformat()}
        self._finish("completed", detail=verification["detail"])
        await self._publish(
            "computer.action",
            {
                "task_id": task_id,
                "conversation_id": conversation_id,
                "action": "press_key",
                "status": "completed",
                "verified": False,
                "detail": verification["detail"],
            },
        )
        return {"key": data, "verification": verification}

    async def hotkey(
        self,
        keys: list[str],
        *,
        task_id: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        self._check_action()
        self._begin("hotkey", task_id=task_id, conversation_id=conversation_id)
        try:
            data = await self._call(self.input.hotkey, keys)
        except Exception as exc:
            self._finish("failed", detail=f"Hotkey failed: {'+'.join(keys)}", error=str(exc))
            raise
        verification = {
            "verified": False,
            "method": "none",
            "detail": f"Hotkey {'+'.join(keys)} was injected; its effect must be verified with a fresh observation.",
        }
        self._last_verification = {**verification, "timestamp": datetime.now(timezone.utc).isoformat()}
        self._finish("completed", detail=verification["detail"])
        await self._publish(
            "computer.action",
            {
                "task_id": task_id,
                "conversation_id": conversation_id,
                "action": "hotkey",
                "status": "completed",
                "verified": False,
                "detail": verification["detail"],
            },
        )
        return {"hotkey": data, "verification": verification}

    async def select_all(self, *, task_id: str | None = None, conversation_id: str | None = None) -> dict[str, Any]:
        return await self.hotkey(["ctrl", "a"], task_id=task_id, conversation_id=conversation_id)

    async def copy(
        self,
        *,
        expected: str | None = None,
        task_id: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        result = await self.hotkey(["ctrl", "c"], task_id=task_id, conversation_id=conversation_id)
        if expected is not None:
            # Windows updates the clipboard asynchronously after Ctrl+C.
            await asyncio.sleep(0.3)
            verification = await self._call(verify_clipboard, self.input, expected)
            result["verification"] = verification
            self._last_verification = {**verification, "timestamp": datetime.now(timezone.utc).isoformat()}
            await self._publish(
                "computer.verification",
                {
                    "task_id": task_id,
                    "conversation_id": conversation_id,
                    "action": "copy",
                    "status": "completed",
                    "verified": bool(verification.get("verified")),
                    "detail": verification.get("detail"),
                },
            )
        return result

    async def paste_text(
        self,
        text: str,
        *,
        verify: bool = True,
        window_name: str | None = None,
        window_handle: int | None = None,
        task_id: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        self._check_action()
        target = await self._ensure_target_window(window_name, window_handle)
        self._begin("paste_text", task_id=task_id, conversation_id=conversation_id)
        try:
            data = await self._call(self.input.paste_text, text)
        except Exception as exc:
            self._finish("failed", detail="Paste failed", error=str(exc))
            raise
        if target:
            data["target_window"] = target.get("title")
            data["target_handle"] = target.get("handle")
        self._activity["status"] = "verifying"
        if verify:
            verification = await self._call(
                verify_text,
                self.observer,
                text,
                window_title=window_name,
                window_handle=(target or {}).get("handle"),
            )
        else:
            verification = {
                "verified": False,
                "method": "clipboard",
                "detail": "Text was placed on the clipboard and pasted; document content was not re-observed.",
            }
        self._last_verification = {**verification, "timestamp": datetime.now(timezone.utc).isoformat()}
        self._finish("completed", detail=verification.get("detail", ""))
        await self._publish(
            "computer.verification",
            {
                "task_id": task_id,
                "conversation_id": conversation_id,
                "action": "paste_text",
                "status": "completed",
                "verified": bool(verification.get("verified")),
                "detail": verification.get("detail"),
            },
        )
        return {"input": data, "verification": verification}

    async def read_clipboard(self) -> str:
        return await self._call(self.input.get_clipboard)

    async def verify_text(
        self,
        expected: str,
        *,
        window_name: str | None = None,
        window_handle: int | None = None,
        task_id: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        self._begin("verify_text", task_id=task_id, conversation_id=conversation_id)
        self._activity["status"] = "verifying"
        verification = await self._call(
            verify_text,
            self.observer,
            expected,
            window_title=window_name,
            window_handle=window_handle,
        )
        self._last_verification = {**verification, "timestamp": datetime.now(timezone.utc).isoformat()}
        self._finish("completed" if verification.get("verified") else "failed", detail=verification.get("detail", ""))
        await self._publish(
            "computer.verification",
            {
                "task_id": task_id,
                "conversation_id": conversation_id,
                "action": "verify_text",
                "status": "completed",
                "verified": bool(verification.get("verified")),
                "detail": verification.get("detail"),
                "evidence": verification.get("evidence"),
            },
        )
        return verification
