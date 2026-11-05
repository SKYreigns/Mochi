"""Post-action verification.

Verification is evidence-based. A click that was successfully injected is not a
verified outcome; only a fresh observation (active window, accessibility text,
clipboard or OCR) can mark an action verified.
"""

from __future__ import annotations

import re
import time
from typing import Any

from app.core.logging import get_logger

log = get_logger("computer.verify")

_EDIT_ROLES = {"edit", "document", "textbox", "text"}


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def verify_active_window(
    observer: Any,
    title_contains: str,
    *,
    attempts: int = 8,
    interval: float = 0.2,
) -> dict[str, Any]:
    needle = title_contains.strip().lower()
    last_title = ""
    for attempt in range(max(1, attempts)):
        try:
            active = observer.active_window()
        except Exception as exc:
            return {
                "verified": False,
                "method": "window",
                "detail": f"Could not read the active window: {exc}",
                "evidence": "",
            }
        last_title = str(active.get("title", ""))
        if needle and needle in last_title.lower():
            return {
                "verified": True,
                "method": "window",
                "detail": f"Active window title contains {title_contains!r}.",
                "evidence": f"Active window: {last_title!r}",
                "window": active,
            }
        if attempt + 1 < attempts:
            time.sleep(interval)
    return {
        "verified": False,
        "method": "window",
        "detail": f"Active window {last_title!r} does not contain {title_contains!r}.",
        "evidence": "",
        "window": {"title": last_title},
    }


def _uia_text_for_window(
    observer: Any,
    window_title: str | None,
    window_handle: int | None = None,
) -> list[tuple[str, str]]:
    """Return (role, value) pairs for text-bearing UIA controls."""
    if not observer.capabilities().get("uia"):
        return []
    try:
        from pywinauto import Desktop

        desktop = Desktop(backend="uia")
        if window_handle:
            # A window handle is stable; the title is not (editors prefix it
            # with ``*`` once the document becomes dirty).
            window = desktop.window(handle=window_handle)
        elif window_title:
            window = desktop.window(title_re=f".*{re.escape(window_title)}.*")
        else:
            active = observer.active_window()
            window = desktop.window(handle=active.get("handle"))
        window.wait("exists", timeout=3)
        controls = window.descendants()
    except Exception as exc:
        log.info("UIA text inspection failed: %s", exc)
        return []

    values: list[tuple[str, str]] = []
    for control in controls:
        try:
            info = control.element_info
            role = str(getattr(info, "control_type", "") or "").lower()
            if role not in _EDIT_ROLES:
                continue
            value = ""
            for getter in (
                lambda: control.get_value(),
                lambda: control.window_text(),
                lambda: getattr(info, "value", ""),
            ):
                try:
                    candidate = getter()
                except Exception:
                    continue
                if candidate:
                    value = str(candidate)
                    break
            if value:
                values.append((role, value))
        except Exception:
            continue
    return values


def verify_text(
    observer: Any,
    expected: str,
    *,
    window_title: str | None = None,
    window_handle: int | None = None,
    allow_ocr: bool = True,
    attempts: int = 3,
    interval: float = 0.35,
) -> dict[str, Any]:
    needle = _normalize(expected)
    if not needle:
        return {"verified": False, "method": "text", "detail": "Expected text is empty.", "evidence": ""}

    for attempt in range(max(1, attempts)):
        values = _uia_text_for_window(observer, window_title, window_handle)
        for role, value in values:
            if needle in _normalize(value):
                return {
                    "verified": True,
                    "method": "uia",
                    "detail": f"Expected text found in a {role or 'text'} control.",
                    "evidence": f"UIA {role or 'text'} control contains the expected text.",
                    "window_title": window_title,
                }
        if values:
            excerpt = _normalize(values[0][1])[:180]
            detail = "Expected text was not found in the window's accessibility text."
            if attempt + 1 < attempts:
                time.sleep(interval)
                continue
            return {
                "verified": False,
                "method": "uia",
                "detail": detail,
                "evidence": f"Observed text starts with: {excerpt!r}",
                "window_title": window_title,
            }

        if allow_ocr and observer.capabilities().get("ocr"):
            try:
                ocr = observer.ocr(0)
                if needle in _normalize(ocr.get("text", "")):
                    return {
                        "verified": True,
                        "method": "ocr",
                        "detail": "Expected text found in OCR of the current screen.",
                        "evidence": "Tesseract OCR contains the expected text.",
                        "window_title": window_title,
                    }
            except Exception as exc:
                log.info("OCR verification failed: %s", exc)

        if attempt + 1 < attempts:
            time.sleep(interval)

    method = "uia+ocr" if allow_ocr and observer.capabilities().get("ocr") else "uia" if observer.capabilities().get("uia") else "none"
    if method == "none":
        detail = (
            "Text could not be verified: UI Automation text and OCR are both unavailable."
        )
    else:
        detail = "Text could not be verified from a fresh observation."
    return {
        "verified": False,
        "method": method,
        "detail": detail,
        "evidence": "",
        "window_title": window_title,
    }


def verify_clipboard(input_controller: Any, expected: str) -> dict[str, Any]:
    try:
        value = input_controller.get_clipboard()
    except Exception as exc:
        return {
            "verified": False,
            "method": "clipboard",
            "detail": f"Clipboard could not be read: {exc}",
            "evidence": "",
        }
    if _normalize(value) == _normalize(expected):
        return {
            "verified": True,
            "method": "clipboard",
            "detail": "Clipboard content matches the expected text.",
            "evidence": f"Clipboard contains {len(value)} characters.",
        }
    return {
        "verified": False,
        "method": "clipboard",
        "detail": "Clipboard content does not match the expected text.",
        "evidence": f"Clipboard contains {len(value)} characters.",
    }


def verify_element(observer: Any, label: str, *, window_title: str | None = None) -> dict[str, Any]:
    element = observer.find_element(label, window_title=window_title)
    if element is None:
        return {
            "verified": False,
            "method": "uia-element",
            "detail": f"Element {label!r} was not found in the current accessibility tree.",
            "evidence": "",
        }
    return {
        "verified": True,
        "method": "uia-element",
        "detail": f"Element {label!r} is present.",
        "evidence": f"Role: {element.get('role')}",
        "element": element,
    }
