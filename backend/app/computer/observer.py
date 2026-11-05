"""Desktop observation: windows, screen geometry, UI Automation tree and OCR.

Everything here is read-only. Observations are untrusted data: text found in a
window is never treated as an instruction by the engine.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.logging import get_logger

log = get_logger("computer.observer")


class DesktopObserver:
    """Read-only desktop observation built on the existing DesktopManager."""

    def __init__(self, desktop: Any) -> None:
        self.desktop = desktop

    # -- capabilities ------------------------------------------------------
    def capabilities(self) -> dict[str, bool]:
        capabilities = {
            "screenshot": _module_available("mss") and _module_available("PIL"),
            "uia": _module_available("pywinauto"),
            "win32": _module_available("win32gui"),
            "ocr": self.ocr_available(),
            "clipboard": _module_available("win32clipboard"),
            "input": _module_available("pywinauto"),
        }
        return capabilities

    def ocr_available(self) -> bool:
        try:
            import pytesseract  # noqa: F401
            from PIL import Image  # noqa: F401
        except Exception:
            return False
        settings = getattr(self.desktop, "settings", None)
        configured = getattr(settings, "tesseract_cmd", "") if settings else ""
        if configured and Path(configured).exists():
            return True
        import shutil

        if shutil.which("tesseract"):
            return True
        return any(
            Path(candidate).exists()
            for candidate in (
                r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            )
        )

    # -- screen ------------------------------------------------------------
    def screens(self) -> dict[str, Any]:
        if _module_available("mss"):
            try:
                import mss

                with mss.mss() as capture:
                    monitors = [dict(item) for item in capture.monitors[1:]]
                    if not monitors:
                        monitors = [dict(item) for item in capture.monitors]
                primary = next(
                    (index for index, item in enumerate(monitors) if item.get("left") == 0 and item.get("top") == 0),
                    0,
                )
                return {
                    "monitors": monitors,
                    "primary": primary + 1,
                    "count": len(monitors),
                    "source": "mss",
                }
            except Exception as exc:  # pragma: no cover - capture boundary
                log.warning("mss monitor discovery failed: %s", exc)
        try:
            import win32api

            width = win32api.GetSystemMetrics(0)
            height = win32api.GetSystemMetrics(1)
            return {
                "monitors": [{"left": 0, "top": 0, "width": width, "height": height}],
                "primary": 1,
                "count": 1,
                "source": "win32",
            }
        except Exception as exc:
            raise RuntimeError(f"Screen geometry is unavailable: {exc}") from exc

    def screenshot(self, monitor: int = 0) -> dict[str, Any]:
        return self.desktop.screenshot(monitor)

    def ocr(self, monitor: int = 0) -> dict[str, Any]:
        if not self.ocr_available():
            raise RuntimeError(
                "Tesseract OCR is not installed. Install it or set JARVIS_TESSERACT_CMD to enable OCR."
            )
        return self.desktop.get_screen_text(monitor)

    # -- windows -----------------------------------------------------------
    def active_window(self) -> dict[str, Any]:
        return self.desktop.active_window()

    def list_windows(self) -> list[dict[str, Any]]:
        return self.desktop.list_windows()

    def window_for_title(self, title_contains: str) -> dict[str, Any] | None:
        needle = title_contains.strip().lower()
        if not needle:
            return None
        windows = self.list_windows()
        exact = [item for item in windows if needle == str(item.get("title", "")).lower()]
        if exact:
            return exact[0]
        matches = [item for item in windows if needle in str(item.get("title", "")).lower()]
        return matches[0] if matches else None

    # -- UI Automation -----------------------------------------------------
    def elements(
        self,
        *,
        window_title: str | None = None,
        limit: int = 80,
        max_depth: int = 6,
    ) -> dict[str, Any]:
        """Enumerate semantic controls for the active (or matching) window.

        Returns an honest empty result plus a warning when UIA is unavailable.
        Elements are data, never instructions.
        """
        if not _module_available("pywinauto"):
            return {
                "elements": [],
                "source": "uia",
                "available": False,
                "warning": "pywinauto/UI Automation is unavailable.",
            }
        try:
            from pywinauto import Desktop

            desktop = Desktop(backend="uia")
            if window_title:
                window = desktop.window(title_re=f".*{re.escape(window_title)}.*")
            else:
                active = self.active_window()
                window = desktop.window(handle=active.get("handle"))
            window.wait("exists", timeout=3)
        except Exception as exc:
            return {
                "elements": [],
                "source": "uia",
                "available": False,
                "warning": f"UI Automation inspection failed: {exc}",
            }

        collected: list[dict[str, Any]] = []

        def walk(control: Any, depth: int) -> None:
            if depth > max_depth or len(collected) >= limit:
                return
            try:
                children = control.children()
            except Exception:
                return
            for child in children:
                if len(collected) >= limit:
                    return
                try:
                    info = child.element_info
                    name = (getattr(info, "name", "") or "").strip()
                    control_type = (getattr(info, "control_type", "") or "").strip()
                    if name:
                        rect = child.rectangle()
                        collected.append(
                            {
                                "label": name[:300],
                                "role": control_type or "unknown",
                                "automation_id": (getattr(info, "automation_id", "") or "")[:200],
                                "class_name": (getattr(info, "class_name", "") or "")[:200],
                                "rect": {
                                    "left": int(rect.left),
                                    "top": int(rect.top),
                                    "right": int(rect.right),
                                    "bottom": int(rect.bottom),
                                },
                                "enabled": bool(getattr(info, "is_enabled", True)),
                                "source": "uia",
                                "confidence": 1.0,
                            }
                        )
                except Exception:
                    continue
                walk(child, depth + 1)

        walk(window, 0)
        # Give each element a stable reference; coordinate clicks are still
        # resolved from the current UIA rectangle at action time.
        for index, element in enumerate(collected):
            element["element_id"] = f"uia-{index}"
        return {
            "elements": collected,
            "source": "uia",
            "available": True,
            "window_title": window_title or self.active_window().get("title", ""),
            "truncated": len(collected) >= limit,
        }

    def element_at(self, x: int, y: int) -> dict[str, Any] | None:
        if not _module_available("pywinauto"):
            return None
        try:
            from pywinauto import Desktop

            control = Desktop(backend="uia").from_point(x, y)
            info = control.element_info
            name = (getattr(info, "name", "") or "").strip()
            if not name:
                return None
            return {
                "label": name[:300],
                "role": (getattr(info, "control_type", "") or "unknown"),
                "source": "uia",
            }
        except Exception:
            return None

    def find_element(
        self,
        label: str,
        *,
        window_title: str | None = None,
        role: str | None = None,
    ) -> dict[str, Any] | None:
        needle = label.strip().lower()
        if not needle:
            return None
        result = self.elements(window_title=window_title, limit=200)
        candidates = result.get("elements", [])
        if role:
            role_needle = role.strip().lower()
            candidates = [item for item in candidates if role_needle in str(item.get("role", "")).lower()]
        exact = [item for item in candidates if str(item.get("label", "")).strip().lower() == needle]
        if exact:
            return exact[0]
        partial = [item for item in candidates if needle in str(item.get("label", "")).strip().lower()]
        if not partial:
            return None
        return sorted(partial, key=lambda item: len(str(item.get("label", ""))))[0]

    # -- combined observation ---------------------------------------------
    def observe(
        self,
        *,
        include_elements: bool = True,
        include_ocr: bool = False,
        include_screenshot: bool = True,
        window_title: str | None = None,
        element_limit: int = 60,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        warnings: list[str] = []
        capabilities = self.capabilities()
        screen: dict[str, Any] = {}
        try:
            screen = self.screens()
        except Exception as exc:
            warnings.append(str(exc))

        active: dict[str, Any] = {}
        windows: list[dict[str, Any]] = []
        try:
            active = self.active_window()
            windows = self.list_windows()
        except Exception as exc:
            warnings.append(f"Window enumeration failed: {exc}")

        active_monitor = _monitor_for_window(active, screen.get("monitors", []))

        screenshot: dict[str, Any] | None = None
        if include_screenshot:
            if capabilities.get("screenshot"):
                try:
                    screenshot = self.screenshot(0)
                except Exception as exc:
                    warnings.append(f"Screenshot failed: {exc}")
            else:
                warnings.append("Screenshot capture is unavailable (mss/Pillow missing).")

        elements: list[dict[str, Any]] = []
        uia_available = capabilities.get("uia", False)
        if include_elements:
            result = self.elements(window_title=window_title, limit=element_limit)
            elements = result.get("elements", [])
            uia_available = bool(result.get("available"))
            if result.get("warning"):
                warnings.append(str(result["warning"]))

        ocr: dict[str, Any] | None = None
        if include_ocr:
            if capabilities.get("ocr"):
                try:
                    text = self.ocr(0)
                    ocr = {"text": text.get("text", "")[:20000], "chars": text.get("chars", 0), "source": "tesseract"}
                except Exception as exc:
                    warnings.append(f"OCR failed: {exc}")
            else:
                warnings.append("OCR is unavailable: Tesseract is not installed.")

        sources = ["window_metadata"]
        if include_elements and uia_available:
            sources.append("uia")
        if screenshot:
            sources.append("screenshot")
        if ocr:
            sources.append("ocr")

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "screen": {
                "width": (screen.get("monitors") or [{}])[0].get("width"),
                "height": (screen.get("monitors") or [{}])[0].get("height"),
                "monitors": screen.get("monitors", []),
                "count": screen.get("count", 0),
                "primary": screen.get("primary", 1),
                "source": screen.get("source"),
            },
            "active_monitor": active_monitor,
            "active_window": active,
            "windows": windows,
            "window_count": len(windows),
            "elements": elements,
            "element_count": len(elements),
            "detection_sources": sources,
            "capabilities": capabilities,
            "screenshot": screenshot,
            "ocr": ocr,
            "warnings": warnings,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            "untrusted": True,
        }


def _monitor_for_window(window: dict[str, Any], monitors: list[dict[str, Any]]) -> int:
    rect = window.get("rect") or {}
    try:
        cx = (int(rect.get("left", 0)) + int(rect.get("right", 0))) // 2
        cy = (int(rect.get("top", 0)) + int(rect.get("bottom", 0))) // 2
    except (TypeError, ValueError):
        return 1
    for index, monitor in enumerate(monitors, start=1):
        left = int(monitor.get("left", 0))
        top = int(monitor.get("top", 0))
        width = int(monitor.get("width", 0))
        height = int(monitor.get("height", 0))
        if left <= cx < left + width and top <= cy < top + height:
            return index
    return 1


def _module_available(name: str) -> bool:
    try:
        __import__(name)
        return True
    except Exception:
        return False
