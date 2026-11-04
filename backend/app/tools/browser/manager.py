"""Playwright browser engine with a dedicated persistent profile."""

from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.async_api import BrowserContext, Page, async_playwright

from app.core.config import Settings
from app.core.errors import PolicyViolationError, ToolExecutionError
from app.core.events import EventBus
from app.core.logging import get_logger

log = get_logger("browser")


class BrowserManager:
    """Owns exactly one persistent Chromium context.

    The profile lives under data/browser/profile and is never copied from (or
    shared with) the user's personal browser. No cookie or password extraction
    is implemented.
    """

    def __init__(self, settings: Settings, events: EventBus) -> None:
        self.settings = settings
        self.events = events
        self._playwright: Any = None
        self._context: BrowserContext | None = None
        self._tabs: dict[str, Page] = {}
        self._page_ids: dict[int, str] = {}
        self._page_errors: dict[str, list[str]] = {}
        self._downloads: list[dict[str, Any]] = []
        self._active_tab: str | None = None
        self._lock = asyncio.Lock()
        self.download_dir = settings.data_dir / "downloads" / "browser"
        self.download_dir.mkdir(parents=True, exist_ok=True)

    @property
    def is_running(self) -> bool:
        return self._context is not None

    async def start(self) -> None:
        async with self._lock:
            if self._context is not None:
                return
            profile_dir = Path(self.settings.browser_profile_dir or self.settings.data_dir / "browser" / "profile")
            profile_dir.mkdir(parents=True, exist_ok=True)
            self._playwright = await async_playwright().start()
            try:
                self._context = await self._playwright.chromium.launch_persistent_context(
                    user_data_dir=str(profile_dir),
                    headless=self.settings.browser_headless,
                    accept_downloads=True,
                    no_viewport=True,
                    args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
                )
            except Exception as exc:
                await self._playwright.stop()
                self._playwright = None
                raise ToolExecutionError(
                    f"Could not launch the browser: {exc}",
                    hint="Run `python -m playwright install chromium` and verify the profile directory is writable.",
                ) from exc

            self._context.on("page", self._on_page)
            for page in self._context.pages:
                self._register_page(page)
            if not self._tabs:
                page = await self._context.new_page()
                self._register_page(page)
            else:
                self._active_tab = next(iter(self._tabs))
            log.info("Browser started with %d tab(s)", len(self._tabs))
            await self.events.publish("browser.started", {"tabs": len(self._tabs)})

    def _on_page(self, page: Page) -> None:
        self._register_page(page)

    def _register_page(self, page: Page) -> str:
        existing = self._page_ids.get(id(page))
        if existing and existing in self._tabs:
            return existing
        tab_id = f"tab_{uuid.uuid4().hex[:8]}"
        self._tabs[tab_id] = page
        self._page_ids[id(page)] = tab_id
        self._page_errors[tab_id] = []
        self._active_tab = tab_id
        page.on("pageerror", lambda error: self._page_errors.setdefault(tab_id, []).append(str(error)[:500]))
        page.on("close", lambda _: self._forget_page(tab_id))
        try:
            page.on("download", lambda download: asyncio.create_task(self._handle_download(tab_id, download)))
        except Exception:
            pass
        return tab_id

    def _forget_page(self, tab_id: str) -> None:
        self._tabs.pop(tab_id, None)
        self._page_errors.pop(tab_id, None)
        if self._active_tab == tab_id:
            self._active_tab = next(iter(self._tabs), None)

    async def _handle_download(self, tab_id: str, download: Any) -> None:
        try:
            suggested = download.suggested_filename
            target = self.download_dir / _safe_filename(suggested)
            target = _unique_path(target)
            await download.save_as(str(target))
            record = {
                "tab_id": tab_id,
                "filename": target.name,
                "path": str(target),
                "size": target.stat().st_size if target.exists() else 0,
                "url": download.url,
                "at": time.time(),
            }
            self._downloads.append(record)
            await self.events.publish("browser.download", record)
        except Exception as exc:  # pragma: no cover
            log.warning("Download capture failed: %s", exc)

    async def _page(self, tab_id: str | None = None) -> tuple[str, Page]:
        await self.start()
        assert self._context is not None
        if tab_id:
            page = self._tabs.get(tab_id)
            if page is None or page.is_closed():
                raise ToolExecutionError(f"Tab '{tab_id}' is no longer open.", hint="Call browser.list_tabs to refresh.")
            self._active_tab = tab_id
            return tab_id, page
        if self._active_tab and self._active_tab in self._tabs:
            return self._active_tab, self._tabs[self._active_tab]
        if self._tabs:
            self._active_tab = next(iter(self._tabs))
            return self._active_tab, self._tabs[self._active_tab]
        page = await self._context.new_page()
        return self._register_page(page), page

    @staticmethod
    def _validate_url(url: str, blocked: list[str]) -> str:
        parsed = urlparse(url.strip())
        if parsed.scheme not in ("http", "https"):
            raise PolicyViolationError(
                "Only http(s) URLs can be opened.",
                hint="file:// and javascript: URLs are blocked.",
            )
        host = (parsed.hostname or "").lower()
        for blocked_site in blocked:
            blocked_site = blocked_site.strip().lower()
            if blocked_site and (host == blocked_site or host.endswith(f".{blocked_site}")):
                raise PolicyViolationError(f"'{host}' is on the configured blocked website list.")
        return url.strip()

    async def open_url(self, url: str, tab_id: str | None = None, *, new_tab: bool = False) -> dict[str, Any]:
        blocked = list(self.settings_safety_blocked())
        safe_url = self._validate_url(url, blocked)
        await self.start()
        assert self._context is not None
        if tab_id and not new_tab:
            _, page = await self._page(tab_id)
        else:
            page = await self._context.new_page()
            tab_id = self._register_page(page)
        try:
            response = await page.goto(safe_url, wait_until="domcontentloaded", timeout=45_000)
        except Exception as exc:
            raise ToolExecutionError(
                f"Navigation to {safe_url} failed: {exc}",
                hint="Check the URL, network connection, or whether the site blocks automated browsers.",
            ) from exc
        await page.wait_for_timeout(400)
        title = await _safe_title(page)
        status = response.status if response else None
        return {
            "tab_id": tab_id,
            "url": page.url,
            "title": title,
            "http_status": status,
            "errors": self._page_errors.get(tab_id, [])[-3:],
        }

    def settings_safety_blocked(self) -> list[str]:
        # Filled by the tools layer through `set_blocked_sites`; kept here so the
        # manager has no dependency on the settings service.
        return list(getattr(self, "_blocked_sites", []))

    def set_blocked_sites(self, sites: list[str]) -> None:
        self._blocked_sites = sites

    async def list_tabs(self) -> list[dict[str, Any]]:
        await self.start()
        tabs: list[dict[str, Any]] = []
        for tab_id, page in list(self._tabs.items()):
            if page.is_closed():
                self._forget_page(tab_id)
                continue
            tabs.append(
                {
                    "tab_id": tab_id,
                    "url": page.url,
                    "title": await _safe_title(page),
                    "active": tab_id == self._active_tab,
                }
            )
        return tabs

    async def close_tab(self, tab_id: str) -> dict[str, Any]:
        _, page = await self._page(tab_id)
        url = page.url
        await page.close()
        self._forget_page(tab_id)
        return {"closed": tab_id, "url": url}

    async def get_url(self, tab_id: str | None = None) -> dict[str, Any]:
        actual_id, page = await self._page(tab_id)
        return {"tab_id": actual_id, "url": page.url}

    async def get_title(self, tab_id: str | None = None) -> dict[str, Any]:
        actual_id, page = await self._page(tab_id)
        return {"tab_id": actual_id, "title": await _safe_title(page)}

    async def snapshot(self, tab_id: str | None = None, max_chars: int = 9000) -> dict[str, Any]:
        actual_id, page = await self._page(tab_id)
        text = await _safe_inner_text(page)
        interactive = await page.evaluate(_INTERACTIVE_JS)
        forms = await page.evaluate(_FORMS_JS)
        return {
            "tab_id": actual_id,
            "url": page.url,
            "title": await _safe_title(page),
            "text": text[:max_chars],
            "text_truncated": len(text) > max_chars,
            "interactive": interactive[:200],
            "forms": forms[:20],
            "errors": self._page_errors.get(actual_id, [])[-5:],
        }

    async def screenshot(self, tab_id: str | None = None, full_page: bool = False) -> dict[str, Any]:
        actual_id, page = await self._page(tab_id)
        filename = f"browser_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.png"
        target = Path(self.settings.screenshots_dir or self.settings.data_dir / "screenshots") / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(target), full_page=full_page)
        record = {
            "path": str(target),
            "url": f"/api/live/screenshots/{filename}",
            "tab_id": actual_id,
            "page_url": page.url,
            "title": await _safe_title(page),
            "size": target.stat().st_size,
        }
        await self.events.publish("browser.screenshot", record)
        return record

    async def _locator(self, page: Page, selector: str) -> Any:
        locator = page.locator(selector).first
        try:
            await locator.wait_for(state="visible", timeout=10_000)
            await locator.scroll_into_view_if_needed(timeout=5_000)
        except Exception as exc:
            raise ToolExecutionError(
                f"Element not found or not visible: {selector}",
                hint="Take a fresh snapshot and locate the element again; the page may have changed.",
            ) from exc
        return locator

    async def click(self, selector: str, tab_id: str | None = None) -> dict[str, Any]:
        actual_id, page = await self._page(tab_id)
        before_url = page.url
        locator = await self._locator(page, selector)
        await locator.click(timeout=10_000)
        await page.wait_for_timeout(500)
        return {
            "tab_id": actual_id,
            "selector": selector,
            "url_before": before_url,
            "url_after": page.url,
            "navigated": before_url != page.url,
            "title": await _safe_title(page),
            "errors": self._page_errors.get(actual_id, [])[-3:],
        }

    async def type_text(self, selector: str, text: str, tab_id: str | None = None, clear: bool = True) -> dict[str, Any]:
        actual_id, page = await self._page(tab_id)
        locator = await self._locator(page, selector)
        if clear:
            try:
                await locator.fill("", timeout=5_000)
            except Exception:
                pass
        await locator.type(text, delay=12, timeout=15_000)
        value = ""
        try:
            value = await locator.input_value(timeout=2_000)
        except Exception:
            pass
        return {"tab_id": actual_id, "selector": selector, "typed_chars": len(text), "value": value}

    async def fill_input(self, selector: str, text: str, tab_id: str | None = None) -> dict[str, Any]:
        actual_id, page = await self._page(tab_id)
        locator = await self._locator(page, selector)
        await locator.fill(text, timeout=10_000)
        value = await locator.input_value(timeout=3_000)
        return {"tab_id": actual_id, "selector": selector, "value": value}

    async def select_option(self, selector: str, value: str, tab_id: str | None = None) -> dict[str, Any]:
        actual_id, page = await self._page(tab_id)
        locator = await self._locator(page, selector)
        selected = await locator.select_option(value=value, timeout=8_000)
        return {"tab_id": actual_id, "selector": selector, "selected": selected}

    async def press_key(self, key: str, tab_id: str | None = None) -> dict[str, Any]:
        actual_id, page = await self._page(tab_id)
        await page.keyboard.press(key)
        await page.wait_for_timeout(250)
        return {"tab_id": actual_id, "key": key, "url": page.url, "title": await _safe_title(page)}

    async def scroll(self, direction: str, amount: int = 600, tab_id: str | None = None) -> dict[str, Any]:
        actual_id, page = await self._page(tab_id)
        if direction not in ("up", "down"):
            raise ToolExecutionError("direction must be 'up' or 'down'")
        delta = max(1, min(20_000, amount))
        await page.mouse.wheel(0, delta if direction == "down" else -delta)
        await page.wait_for_timeout(300)
        position = await page.evaluate("() => ({x: window.scrollX, y: window.scrollY})")
        return {"tab_id": actual_id, "direction": direction, "amount": delta, "scroll": position}

    async def extract_text(self, tab_id: str | None = None, selectors: str | None = None) -> dict[str, Any]:
        actual_id, page = await self._page(tab_id)
        if selectors:
            locator = page.locator(selectors)
            try:
                text = await locator.first.inner_text(timeout=8_000)
            except Exception as exc:
                raise ToolExecutionError(f"Extraction failed for selector '{selectors}': {exc}") from exc
            return {"tab_id": actual_id, "selector": selectors, "text": text[:20_000], "url": page.url}
        text = await _safe_inner_text(page)
        return {"tab_id": actual_id, "text": text[:20_000], "url": page.url, "title": await _safe_title(page)}

    async def find_element(self, description: str, tab_id: str | None = None) -> list[dict[str, Any]]:
        actual_id, page = await self._page(tab_id)
        candidates = await page.evaluate(_FIND_JS, {"query": description.lower()[:120]})
        return candidates[:15]

    async def collect_links(
        self,
        tab_id: str | None = None,
        selector: str | None = None,
        limit: int = 400,
    ) -> list[dict[str, Any]]:
        """Collect anchors with nearby row text (used by LMS heuristics)."""
        actual_id, page = await self._page(tab_id)
        links = await page.evaluate(
            _LINKS_JS,
            {"selector": selector or "a[href]", "limit": limit},
        )
        return links

    async def count(self, selector: str, tab_id: str | None = None) -> int:
        actual_id, page = await self._page(tab_id)
        try:
            return await page.locator(selector).count()
        except Exception:
            return 0

    async def exists(self, selector: str, tab_id: str | None = None) -> bool:
        return await self.count(selector, tab_id) > 0

    async def download(self, url: str, tab_id: str | None = None) -> dict[str, Any]:
        blocked = self.settings_safety_blocked()
        safe_url = self._validate_url(url, blocked)
        await self.start()
        assert self._context is not None
        actual_id, page = await self._page(tab_id)
        before = set(self.download_dir.iterdir())
        try:
            await page.evaluate(
                """
                (url) => {
                    const a = document.createElement('a');
                    a.href = url; a.download = ''; a.style.display = 'none';
                    document.body.appendChild(a); a.click(); a.remove();
                }
                """,
                safe_url,
            )
        except Exception:
            pass
        deadline = time.time() + 45
        while time.time() < deadline:
            new_files = [f for f in self.download_dir.iterdir() if f not in before and f.is_file()]
            if new_files:
                newest = max(new_files, key=lambda f: f.stat().st_mtime)
                return {
                    "tab_id": actual_id,
                    "path": str(newest),
                    "filename": newest.name,
                    "size": newest.stat().st_size,
                    "url": safe_url,
                }
            await asyncio.sleep(0.5)
        # Fallback: authenticated HTTP fetch through the browser context.
        try:
            response = await self._context.request.get(safe_url, timeout=60_000)
            if not response.ok:
                raise ToolExecutionError(f"Download request returned HTTP {response.status}.")
            body = await response.body()
            name = _safe_filename(urlparse(safe_url).path.rsplit("/", 1)[-1] or "download.bin")
            target = _unique_path(self.download_dir / name)
            target.write_bytes(body)
            return {
                "tab_id": actual_id,
                "path": str(target),
                "filename": target.name,
                "size": target.stat().st_size,
                "url": safe_url,
            }
        except Exception as exc:
            raise ToolExecutionError(
                f"Download did not produce a file: {exc}",
                hint="The link may require an in-page click, a login, or the site may block direct downloads.",
            ) from exc

    async def upload_file(self, selector: str, file_path: str, tab_id: str | None = None) -> dict[str, Any]:
        actual_id, page = await self._page(tab_id)
        locator = page.locator(selector).first
        try:
            await locator.set_input_files(file_path, timeout=15_000)
        except Exception as exc:
            raise ToolExecutionError(
                f"Could not attach '{file_path}': {exc}",
                hint="Verify the input is a file input and the file exists.",
            ) from exc
        names = await locator.evaluate("(el) => el.files ? Array.from(el.files).map(f => f.name) : []")
        return {"tab_id": actual_id, "selector": selector, "attached": names}

    async def state(self) -> dict[str, Any]:
        if not self.is_running:
            return {"running": False, "tabs": [], "active": None}
        tabs = await self.list_tabs()
        active = next((tab for tab in tabs if tab.get("active")), tabs[0] if tabs else None)
        return {"running": True, "tabs": tabs, "active": active}

    async def close(self) -> None:
        try:
            if self._context is not None:
                await self._context.close()
        except Exception:
            pass
        self._context = None
        self._tabs.clear()
        self._page_ids.clear()
        self._active_tab = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
        log.info("Browser closed")


async def _safe_title(page: Page) -> str:
    try:
        return await page.title()
    except Exception:
        return ""


async def _safe_inner_text(page: Page) -> str:
    try:
        return await page.inner_text("body", timeout=8_000)
    except Exception:
        return ""


def _safe_filename(name: str) -> str:
    keep = "".join(ch for ch in name if ch.isalnum() or ch in " ._-()[]").strip()
    return keep[:180] or "download.bin"


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for index in range(1, 1000):
        candidate = path.with_name(f"{stem} ({index}){suffix}")
        if not candidate.exists():
            return candidate
    return path.with_name(f"{stem}_{uuid.uuid4().hex[:6]}{suffix}")


_INTERACTIVE_JS = """
() => {
  const selectorFor = (el) => {
    if (el.id) return `#${CSS.escape(el.id)}`;
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && parts.length < 6) {
      let part = node.tagName.toLowerCase();
      if (node.name) part += `[name="${node.name}"]`;
      else if (node.getAttribute && node.getAttribute('type')) part += `[type="${node.getAttribute('type')}"]`;
      else if (node.className && typeof node.className === 'string' && node.className.trim()) {
        part += '.' + node.className.trim().split(/\\s+/).slice(0, 2).map(CSS.escape).join('.');
      }
      const parent = node.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter(c => c.tagName === node.tagName);
        if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(node) + 1})`;
      }
      parts.unshift(part);
      node = parent;
    }
    return parts.join(' > ');
  };
  const items = [];
  const nodes = document.querySelectorAll('a,button,input,select,textarea,[role="button"],[role="link"],[contenteditable="true"],[onclick]');
  for (const el of Array.from(nodes).slice(0, 200)) {
    const rect = el.getBoundingClientRect();
    if (rect.width < 1 || rect.height < 1) continue;
    items.push({
      tag: el.tagName.toLowerCase(),
      type: el.getAttribute('type') || '',
      name: el.getAttribute('name') || '',
      id: el.id || '',
      role: el.getAttribute('role') || '',
      placeholder: el.getAttribute('placeholder') || '',
      aria_label: el.getAttribute('aria-label') || '',
      text: (el.innerText || el.value || '').trim().slice(0, 120),
      href: el.href || '',
      disabled: !!el.disabled,
      selector: selectorFor(el)
    });
  }
  return items;
}
"""

_FORMS_JS = """
() => Array.from(document.forms).slice(0, 20).map(form => ({
  action: form.action || '',
  method: form.method || '',
  fields: Array.from(form.querySelectorAll('input,select,textarea')).slice(0, 30).map(el => ({
    name: el.name || '', type: el.type || el.tagName.toLowerCase(),
    placeholder: el.placeholder || '', required: !!el.required
  }))
}))
"""

_LINKS_JS = """
({selector, limit}) => {
  const links = [];
  const seen = new Set();
  for (const el of Array.from(document.querySelectorAll(selector)).slice(0, limit * 2)) {
    const href = el.href || '';
    if (!/^https?:/i.test(href)) continue;
    const text = (el.innerText || el.getAttribute('aria-label') || el.getAttribute('title') || '').trim();
    const key = href + '|' + text;
    if (!text || seen.has(key)) continue;
    seen.add(key);
    const container = el.closest('tr,li,article,section,div');
    const context = (container ? container.innerText : text).trim().replace(/\\s+/g, ' ').slice(0, 400);
    links.push({
      text: text.slice(0, 240),
      href,
      context,
      title: el.getAttribute('title') || '',
      aria_label: el.getAttribute('aria-label') || '',
      download: el.hasAttribute('download'),
      target: el.getAttribute('target') || ''
    });
  }
  return links;
}
"""

_FIND_JS = """
(query) => {
  const results = [];
  const nodes = document.querySelectorAll('a,button,input,select,textarea,[role="button"],[role="link"],[role="tab"],[role="menuitem"]');
  for (const el of Array.from(nodes).slice(0, 800)) {
    const haystack = [
      el.innerText, el.value, el.getAttribute('aria-label'), el.getAttribute('placeholder'),
      el.getAttribute('name'), el.id, el.getAttribute('title'), el.getAttribute('alt')
    ].filter(Boolean).join(' ').toLowerCase();
    if (!haystack.includes(query)) continue;
    const rect = el.getBoundingClientRect();
    if (rect.width < 1 || rect.height < 1) continue;
    let selector = el.id ? `#${CSS.escape(el.id)}` : '';
    if (!selector) {
      if (el.getAttribute('aria-label')) selector = `[aria-label="${el.getAttribute('aria-label').replace(/"/g, '\\\\"')}"]`;
      else if (el.getAttribute('name')) selector = `${el.tagName.toLowerCase()}[name="${el.getAttribute('name')}"]`;
      else if (el.getAttribute('placeholder')) selector = `[placeholder="${el.getAttribute('placeholder').replace(/"/g, '\\\\"')}"]`;
    }
    const text = (el.innerText || el.value || '').trim().slice(0, 100);
    results.push({
      tag: el.tagName.toLowerCase(), text, selector,
      role: el.getAttribute('role') || '',
      aria_label: el.getAttribute('aria-label') || '',
      suggested_text_locator: text ? `text=${text}` : '',
      x: Math.round(rect.x), y: Math.round(rect.y),
      width: Math.round(rect.width), height: Math.round(rect.height)
    });
  }
  return results;
}
"""
