"""Completion validation: a generated sentence is never proof of execution.

The model may only claim an external/computer action when a matching execution
record exists. This module is deliberately conservative: it does not try to
understand every sentence, it detects first-person or passive past-tense action
claims and checks them against the persisted execution records collected for
the task.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Verbs that assert an external action was performed. Read-only verbs such as
# "found" or "read" are intentionally excluded from the zero-execution check so
# a plain conversational answer is not misclassified.
_ACTION_VERBS = (
    "opened|launched|started|focused|switched|closed|"
    "clicked|double-clicked|right-clicked|moved|dragged|scrolled|"
    "typed|entered|pressed|selected|copied|pasted|"
    "saved|created|wrote|deleted|removed|renamed|moved|downloaded|uploaded|installed|"
    "sent|submitted|purchased|bought|shared|"
    "captured|screenshotted|observed"
)

_ACTIVE_CLAIM = re.compile(
    rf"\b(?:i|we)\b(?:\s+(?:have|had|'ve|’ve))?(?:\s+(?:just|already|successfully|then))?\s+"
    rf"(?:{_ACTION_VERBS})\b",
    re.IGNORECASE,
)
_PASSIVE_CLAIM = re.compile(
    rf"\b(?:has|have|was|were)\s+been\s+(?:{_ACTION_VERBS})\b",
    re.IGNORECASE,
)
# Short completion reports like "Opened Notepad and typed the text." are also
# claims, even without an explicit first-person pronoun.
_BARE_CLAIM = re.compile(
    rf"(?:^|[.!?]\s+)(?:{_ACTION_VERBS})\b",
    re.IGNORECASE,
)

_CLAIM_CATEGORIES: dict[str, tuple[str, ...]] = {
    "opened": ("window",),
    "launched": ("window",),
    "started": ("window",),
    "focused": ("window",),
    "switched": ("window",),
    "closed": ("window",),
    "clicked": ("input",),
    "double-clicked": ("input",),
    "right-clicked": ("input",),
    "moved": ("input", "file"),
    "dragged": ("input",),
    "scrolled": ("input",),
    "typed": ("input",),
    "entered": ("input",),
    "pressed": ("input",),
    "selected": ("input",),
    "copied": ("input", "file"),
    "pasted": ("input",),
    "saved": ("file",),
    "created": ("file",),
    "wrote": ("file",),
    "deleted": ("file",),
    "removed": ("file",),
    "renamed": ("file",),
    "downloaded": ("file",),
    "uploaded": ("file",),
    "installed": ("consequential",),
    "sent": ("consequential",),
    "submitted": ("consequential",),
    "purchased": ("consequential",),
    "bought": ("consequential",),
    "shared": ("consequential",),
    "captured": ("observation",),
    "screenshotted": ("observation",),
    "observed": ("observation",),
}

_TOOL_CATEGORIES: dict[str, tuple[str, ...]] = {
    "window": (
        "desktop.open_application",
        "desktop.focus_window",
        "desktop.close_application",
        "computer.open_application",
        "computer.focus_window",
    ),
    "input": (
        "desktop.click",
        "desktop.double_click",
        "desktop.type_text",
        "desktop.press_key",
        "desktop.hotkey",
        "desktop.scroll",
        "computer.",
    ),
    "file": ("files.", "documents."),
    "observation": (
        "desktop.take_screenshot",
        "desktop.get_screen_text",
        "desktop.get_active_window",
        "desktop.list_windows",
        "computer.observe",
        "computer.screenshot",
    ),
    "consequential": ("lms.submit", "assignments.submit"),
}

_COMPUTER_PREFIXES = ("desktop.", "computer.")


@dataclass(slots=True)
class CompletionValidation:
    ok: bool
    corrected_content: str
    issue: str = ""
    caveats: list[str] = field(default_factory=list)
    claim_categories: list[str] = field(default_factory=list)
    executed_categories: list[str] = field(default_factory=list)

    def caveat_text(self) -> str:
        if not self.caveats:
            return ""
        return "\n\n" + "\n".join(f"> {item}" for item in self.caveats)


def _categories_for_tool(tool_name: str) -> set[str]:
    result: set[str] = set()
    for category, prefixes in _TOOL_CATEGORIES.items():
        if any(tool_name == prefix or tool_name.startswith(prefix) for prefix in prefixes):
            result.add(category)
    return result


def detect_action_claims(content: str) -> tuple[bool, set[str]]:
    """Return whether the text claims a past action and which categories."""
    text = content or ""
    if not text.strip():
        return False, set()
    category_set: set[str] = set()
    matched = False
    for pattern in (_ACTIVE_CLAIM, _PASSIVE_CLAIM, _BARE_CLAIM):
        for match in pattern.finditer(text):
            fragment = match.group(0).lower()
            for verb, categories in _CLAIM_CATEGORIES.items():
                if re.search(rf"\b{re.escape(verb)}\b", fragment):
                    category_set.update(categories)
                    matched = True
    return matched, category_set


def validate_completion(
    content: str,
    executions: list[dict[str, Any]],
    *,
    computer_prefixes: tuple[str, ...] = _COMPUTER_PREFIXES,
) -> CompletionValidation:
    """Validate a final assistant message against execution records.

    ``executions`` entries are the orchestrator's serialized records with
    ``tool``, ``ok`` and ``verified`` keys.
    """
    claims, claim_categories = detect_action_claims(content)
    succeeded = [item for item in executions if item.get("ok")]
    executed_categories: set[str] = set()
    for item in succeeded:
        executed_categories |= _categories_for_tool(str(item.get("tool", "")))

    if claims and not executions:
        return CompletionValidation(
            ok=False,
            corrected_content=(
                "I cannot confirm this action. No tool was executed, so there is no "
                "execution record showing that it happened. I will not report it as completed."
            ),
            issue=(
                "Completion rejected: the response claimed a completed action but zero "
                "tool executions were recorded."
            ),
            claim_categories=sorted(claim_categories),
            executed_categories=[],
        )

    if claims and claim_categories and not (claim_categories & executed_categories):
        missing = ", ".join(sorted(claim_categories))
        recorded = ", ".join(sorted(executed_categories)) or "none"
        return CompletionValidation(
            ok=False,
            corrected_content=(
                "I cannot confirm that the described action was completed. The matching "
                f"execution evidence is missing (claimed: {missing}; recorded: {recorded})."
            ),
            issue=(
                "Completion rejected: the response described an action category "
                f"({missing}) that has no successful matching execution record "
                f"(recorded categories: {recorded})."
            ),
            claim_categories=sorted(claim_categories),
            executed_categories=sorted(executed_categories),
        )

    caveats: list[str] = []
    unverified_computer = [
        item
        for item in succeeded
        if str(item.get("tool", "")).startswith(computer_prefixes) and not item.get("verified")
    ]
    if unverified_computer:
        tools = ", ".join(sorted({str(item["tool"]) for item in unverified_computer}))
        caveats.append(
            "The following computer action(s) executed but could not be independently "
            f"verified: {tools}. Treat their outcome as unconfirmed."
        )

    return CompletionValidation(
        ok=True,
        corrected_content=content,
        caveats=caveats,
        claim_categories=sorted(claim_categories),
        executed_categories=sorted(executed_categories),
    )
