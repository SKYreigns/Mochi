"""Config-driven LMS automation (works with any browser-based LMS).

No institution, URL or selector is hardcoded. Login, CAPTCHA and MFA barriers
are respected: JARVIS pauses and asks the user to authenticate manually.
"""

from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConfigurationError, LoginRequiredError, ToolExecutionError
from app.core.logging import audit
from app.database.models import Assignment
from app.services.settings_service import SettingsService
from app.tools.browser.manager import BrowserManager, _safe_filename, _unique_path

_ASSIGNMENT_HINTS = re.compile(r"(assign|homework|coursework|quiz|exam|project|task|submit|submission)", re.I)
_COURSE_HINTS = re.compile(r"(course|subject|class|module)", re.I)
_DUE_RE = re.compile(
    r"(?:due(?:\s*date)?|deadline|closes?(?:\s*on)?|close\s*date|submission\s*(?:date|deadline))\s*[:\-]?\s*"
    r"([A-Za-z0-9][A-Za-z0-9 ,./:\-]{3,90})",
    re.I,
)
_FILE_HINTS = re.compile(r"\.(pdf|docx?|pptx?|xlsx?|zip|rar|txt|csv|png|jpe?g|mp4|py|ipynb)(\?|$)", re.I)
_DOWNLOAD_HINTS = re.compile(r"(download|resource|file|attachment|pluginfile|mod/resource|mod/folder|content)", re.I)
_LOGIN_URL_HINTS = re.compile(r"(login|signin|sign-in|auth|sso|adfs|oauth)", re.I)
_RECEIPT_HINTS = re.compile(
    r"(submission (?:received|complete|successful)|successfully submitted|has been submitted|"
    r"thank you for your submission|submitted for grading|submission status\s*[:\-]?\s*submitted)",
    re.I,
)


class LmsService:
    def __init__(self, settings: SettingsService, browser: BrowserManager) -> None:
        self.settings = settings
        self.browser = browser
        self._tab_id: str | None = None

    # -- configuration -----------------------------------------------------
    def config(self) -> dict[str, Any]:
        return self.settings.lms

    def selectors(self) -> dict[str, str]:
        selectors = self.config().get("selectors") or {}
        return {key: str(value).strip() for key, value in selectors.items() if str(value).strip()}

    def require_base_url(self) -> str:
        base = str(self.config().get("base_url") or "").strip()
        if not base:
            raise ConfigurationError(
                "No LMS URL is configured.",
                hint="Open Settings → College / LMS and set your institution's LMS URL.",
            )
        return base

    def download_dir(self, subject: str | None = None) -> Path:
        base = Path(self.config().get("download_dir") or self.settings.app.data_dir / "downloads" / "lms")
        if subject:
            base = base / _safe_filename(subject)
        base.mkdir(parents=True, exist_ok=True)
        return base

    # -- navigation --------------------------------------------------------
    async def open(self, url: str | None = None) -> dict[str, Any]:
        target = url or self.require_base_url()
        data = await self.browser.open_url(target, self._tab_id)
        self._tab_id = data["tab_id"]
        return data

    async def snapshot(self, url: str | None = None) -> dict[str, Any]:
        if url:
            await self.open(url)
        elif self._tab_id is None:
            await self.open(self.require_base_url())
        snapshot = await self.browser.snapshot(self._tab_id)
        text = (snapshot.get("text") or "").lower()
        login_form = any(
            item.get("tag") == "input" and item.get("type") == "password"
            for item in snapshot.get("interactive", [])
        )
        challenge = any(
            term in text
            for term in ("captcha", "verify you are human", "two-factor", "multi-factor", "authenticator app", "recaptcha")
        )
        login_url = bool(_LOGIN_URL_HINTS.search(urlparse(snapshot.get("url", "")).path))
        return {
            **snapshot,
            "logged_in": not (login_form or challenge or login_url),
            "login_required": login_form or challenge or login_url,
            "challenge_detected": challenge,
        }

    async def list_courses(self) -> list[dict[str, Any]]:
        await self.open(self.require_base_url())
        selector = self.selectors().get("course_links") or None
        links = await self.browser.collect_links(self._tab_id, selector=selector)
        configured = [str(name).lower() for name in (self.config().get("courses") or [])]
        courses: dict[str, dict[str, Any]] = {}
        for link in links:
            text = link["text"]
            href = link["href"]
            matched = (
                _COURSE_HINTS.search(text)
                or _COURSE_HINTS.search(urlparse(href).path)
                or any(name and (name in text.lower() or name in href.lower()) for name in configured)
            )
            if not matched:
                continue
            if any(skip in href.lower() for skip in ("/login", "/logout", "/profile", "/calendar", "/messages")):
                continue
            if href not in courses:
                courses[href] = {"title": text, "url": href, "context": link.get("context", "")[:240]}
        return list(courses.values())[:60]

    async def list_assignments(self, course_url: str | None = None) -> list[dict[str, Any]]:
        if course_url:
            await self.open(course_url)
        elif self._tab_id is None:
            await self.open(self.require_base_url())
        selector = self.selectors().get("assignment_links") or None
        links = await self.browser.collect_links(self._tab_id, selector=selector)
        assignments: dict[str, dict[str, Any]] = {}
        for link in links:
            text = link["text"]
            href = link["href"]
            if not (_ASSIGNMENT_HINTS.search(text) or _ASSIGNMENT_HINTS.search(urlparse(href).path)):
                continue
            if any(skip in href.lower() for skip in ("/login", "/logout", "help.php")):
                continue
            due_match = _DUE_RE.search(link.get("context") or "")
            due = due_match.group(1).strip()[:100] if due_match else ""
            if href not in assignments:
                assignments[href] = {
                    "title": text[:240],
                    "url": href,
                    "due_hint": due,
                    "context": (link.get("context") or "")[:300],
                }
        return list(assignments.values())[:120]

    async def read_assignment(self, url: str) -> dict[str, Any]:
        await self.open(url)
        snapshot = await self.snapshot()
        if snapshot.get("login_required"):
            raise LoginRequiredError(
                "The LMS requires you to log in before this assignment can be read.",
                hint="Log in manually in the JARVIS browser window, then ask JARVIS to continue.",
            )
        text = snapshot.get("text") or ""
        title = snapshot.get("title") or ""
        if self.selectors().get("assignment_title"):
            extracted = await self.browser.extract_text(self._tab_id, self.selectors()["assignment_title"])
            title = extracted["text"].strip()[:300] or title
        description = text[:10000]
        if self.selectors().get("assignment_description"):
            extracted = await self.browser.extract_text(self._tab_id, self.selectors()["assignment_description"])
            description = extracted["text"][:15000]
        due_match = _DUE_RE.search(text)
        due = due_match.group(1).strip()[:120] if due_match else ""

        attachments: list[dict[str, str]] = []
        for link in await self.browser.collect_links(self._tab_id):
            href = link["href"]
            label = link["text"]
            if _FILE_HINTS.search(href) or _DOWNLOAD_HINTS.search(href) or link.get("download"):
                attachments.append({"title": label[:200], "url": href})
        deduped: dict[str, dict[str, str]] = {}
        for item in attachments:
            deduped.setdefault(item["url"], item)
        return {
            "url": snapshot.get("url"),
            "title": title,
            "due": due,
            "description": description,
            "attachments": list(deduped.values())[:40],
            "page_text": text[:20000],
        }

    async def download_attachment(self, url: str, subject: str | None = None, filename: str | None = None) -> dict[str, Any]:
        data = await self.browser.download(url, self._tab_id)
        source = Path(data["path"])
        target_dir = self.download_dir(subject)
        target = _unique_path(target_dir / _safe_filename(filename or source.name))
        if source.resolve() != target.resolve():
            shutil.move(str(source), str(target))
        if not target.exists() or target.stat().st_size == 0:
            raise ToolExecutionError("The downloaded attachment could not be verified on disk.")
        return {
            "path": str(target),
            "filename": target.name,
            "bytes": target.stat().st_size,
            "url": url,
            "subject": subject or "",
        }

    async def submission_page(self, url: str) -> dict[str, Any]:
        await self.open(url)
        snapshot = await self.snapshot()
        if snapshot.get("login_required"):
            raise LoginRequiredError(
                "Login is required before opening the submission page.",
                hint="Log in manually, then resume.",
            )
        file_inputs = [item for item in snapshot.get("interactive", []) if item.get("tag") == "input" and item.get("type") == "file"]
        buttons = [
            item for item in snapshot.get("interactive", [])
            if re.search(r"(submit|turn in|upload|save changes)", item.get("text") or "", re.I)
        ]
        return {
            "url": snapshot.get("url"),
            "title": snapshot.get("title"),
            "file_inputs": file_inputs,
            "submit_buttons": buttons[:20],
            "text_excerpt": (snapshot.get("text") or "")[:5000],
        }

    async def attach_submission_file(self, url: str | None, selector: str, file_path: str) -> dict[str, Any]:
        if url:
            await self.open(url)
        data = await self.browser.upload_file(selector, file_path, self._tab_id)
        if not data.get("attached"):
            raise ToolExecutionError("The file input did not accept the file.")
        return data

    async def submit(self, session: AsyncSession, url: str, submit_selector: str | None, assignment_id: str | None) -> dict[str, Any]:
        page = await self.submission_page(url)
        selector = submit_selector or self.selectors().get("submit_button") or (
            "button[type='submit'], input[type='submit']"
        )
        before = await self.browser.screenshot(self._tab_id)
        try:
            await self.browser.click(selector, self._tab_id)
        except ToolExecutionError as exc:
            raise ToolExecutionError(
                f"Could not activate the submit button: {exc}",
                hint="Open the submission page and verify the button is present and enabled.",
            ) from exc

        await asyncio.sleep(3.5)
        snapshot = await self.browser.snapshot(self._tab_id)
        text = snapshot.get("text") or ""
        receipt_selector = self.selectors().get("submission_receipt")
        if receipt_selector:
            found = await self.browser.exists(receipt_selector, self._tab_id)
        else:
            found = bool(_RECEIPT_HINTS.search(text))
        after = await self.browser.screenshot(self._tab_id)
        status = "submitted" if found else "unknown"
        result = {
            "submitted": True,
            "receipt_found": found,
            "status": status,
            "url": snapshot.get("url"),
            "evidence": (text[:1500] if found else "No receipt text was detected after submission."),
            "screenshot_before": before,
            "screenshot_after": after,
        }
        if assignment_id:
            assignment = await session.get(Assignment, assignment_id)
            if assignment is not None:
                assignment.submission_status = status
                assignment.status = "submitted" if found else "submission_unverified"
                await session.commit()
        audit("lms.submit", assignment_id=assignment_id, url=url, receipt_found=found)
        return result

    # -- workspace ---------------------------------------------------------
    async def save_assignment(
        self,
        session: AsyncSession,
        *,
        subject: str,
        title: str,
        instructions: str,
        deadline: str | None,
        lms_url: str,
        source_url: str,
        resources: list[dict[str, Any]] | None = None,
    ) -> Assignment:
        existing = None
        if source_url:
            existing = (
                await session.execute(select(Assignment).where(Assignment.source_url == source_url))
            ).scalars().first()
        assignment = existing
        if assignment is None:
            assignment = Assignment(subject=subject, title=title)
            session.add(assignment)
        assignment.subject = subject or assignment.subject
        assignment.title = title or assignment.title
        assignment.instructions = instructions or assignment.instructions
        assignment.deadline = deadline or assignment.deadline
        assignment.lms_url = lms_url or assignment.lms_url
        assignment.source_url = source_url or assignment.source_url
        if resources is not None:
            assignment.resources = resources
        await session.commit()
        return assignment

    async def list_saved(self, session: AsyncSession) -> list[Assignment]:
        return list((await session.execute(select(Assignment).order_by(Assignment.created_at.desc()))).scalars().all())
