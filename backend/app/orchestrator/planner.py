"""LLM-backed task planner with a safe deterministic fallback."""

from __future__ import annotations

import json
import re
from typing import Any

from app.core.logging import get_logger
from app.providers.base import ChatMessage, LLMProvider
from app.orchestrator.prompts import PLANNER_PROMPT

log = get_logger("planner")

# Keyword → tool-domain relevance map. Used to reject a planner selection that
# cannot plausibly serve the user's objective (small models over-select tools).
_DOMAIN_KEYWORDS: dict[str, tuple[str, ...]] = {
    "desktop": (
        "desktop", "screen", "window", "windows", "app", "application", "notepad",
        "calculator", "paint", "explorer", "click", "type", "typing", "keyboard",
        "mouse", "scroll", "focus", "open", "launch", "close", "cursor", "button",
    ),
    "computer": (
        "desktop", "screen", "window", "windows", "app", "application", "notepad",
        "click", "type", "typing", "keyboard", "mouse", "scroll", "focus", "open",
        "launch", "button", "drag", "right-click", "paste", "clipboard",
    ),
    "files": (
        "file", "files", "folder", "directory", "sandbox", "path", "save", "write",
        "read", "create", "delete", "rename", "move", "copy", "list", "organize",
        "disk", "archive",
    ),
    "documents": (
        "document", "documents", "pdf", "docx", "xlsx", "report", "summarize",
        "convert", "spreadsheet", "resume", "letter",
    ),
    "system": (
        "cpu", "memory", "ram", "disk", "process", "processes", "system", "battery",
        "network", "uptime", "os", "metric", "metrics", "performance",
    ),
    "browser": (
        "browser", "web", "website", "page", "tab", "url", "browse", "internet",
        "navigate", "google", "search", "http",
    ),
    "lms": ("lms", "assignment", "assignments", "course", "courses", "canvas", "moodle", "grade", "submit", "submission"),
    "media": ("media", "video", "audio", "song", "youtube", "image", "download"),
    "memory": ("memory", "memories", "remember", "preference", "preferences", "forget", "note", "notes"),
}

_DOMAIN_PREFIXES = {
    "desktop": "desktop.",
    "computer": "computer.",
    "files": "files.",
    "documents": "documents.",
    "system": "system.",
    "browser": "browser.",
    "lms": "lms.",
    "media": "media.",
    "memory": "memory.",
}


def _instruction_domains(instruction: str) -> set[str]:
    text = (instruction or "").lower()
    matched: set[str] = set()
    for domain, keywords in _DOMAIN_KEYWORDS.items():
        for keyword in keywords:
            if re.search(rf"\b{re.escape(keyword)}s?\b", text):
                matched.add(domain)
                break
    # Desktop and computer tools are interchangeable for desktop objectives.
    if "desktop" in matched:
        matched.add("computer")
    if "computer" in matched:
        matched.add("desktop")
    return matched


def _domain_for_tool(name: str) -> str | None:
    for domain, prefix in _DOMAIN_PREFIXES.items():
        if name.startswith(prefix):
            return domain
    return None


def validate_plan(
    plan: dict[str, Any],
    instruction: str,
    tool_names: list[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Reject unrelated tools, prune redundant steps, and annotate the plan.

    The agent only forwards planner-selected tool schemas to the model, so an
    over-selected plan directly removes the tools the task actually needs. When
    relevance cannot be inferred the plan is left intact (only unknown names are
    stripped) rather than guessing.
    """
    available = set(tool_names)
    domains = _instruction_domains(instruction)
    removed_tools: list[dict[str, str]] = []
    removed_steps: list[str] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    steps: list[dict[str, Any]] = []

    for raw in plan.get("steps", []) if isinstance(plan.get("steps"), list) else []:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "Step")[:200]
        raw_tools = raw.get("tools", [])
        tools: list[str] = []
        for name in raw_tools if isinstance(raw_tools, list) else []:
            tool_name = str(name)
            if tool_name not in available:
                removed_tools.append({"tool": tool_name, "reason": "unknown tool"})
                continue
            domain = _domain_for_tool(tool_name)
            if domains and domain is not None and domain not in domains:
                removed_tools.append(
                    {"tool": tool_name, "reason": f"unrelated to objective ({domain})"}
                )
                continue
            if tool_name not in tools:
                tools.append(tool_name)

        had_tools = bool(raw_tools) if isinstance(raw_tools, list) else False
        if had_tools and not tools:
            removed_steps.append(title)
            continue

        signature = (title.lower().strip(), tuple(sorted(tools)))
        if signature in seen:
            removed_steps.append(title)
            continue
        seen.add(signature)
        steps.append(
            {
                "title": title,
                "tools": tools,
                "executed": False,
                "executed_tools": [],
                "status": "pending",
            }
        )

    if not steps:
        # Never leave the agent without a plan; a title-only step still allows
        # the normal agent loop to choose from the full tool catalog.
        steps = [
            {
                "title": "Execute the request with the appropriate tools",
                "tools": [],
                "executed": False,
                "executed_tools": [],
                "status": "pending",
            }
        ]

    validated = {
        **plan,
        "steps": steps,
        "validation": {
            "matched_domains": sorted(domains),
            "removed_tools": removed_tools,
            "removed_steps": removed_steps,
        },
    }
    return validated, validated["validation"]


def record_plan_execution(
    plan: dict[str, Any],
    tool_name: str,
    *,
    ok: bool,
    verified: bool,
) -> None:
    """Mark the plan step(s) that selected ``tool_name`` as executed."""
    for step in plan.get("steps", []):
        if not isinstance(step, dict) or tool_name not in step.get("tools", []):
            continue
        step["executed"] = True
        step["executed_tools"] = list(step.get("executed_tools") or []) + [tool_name]
        if not ok:
            step["status"] = "failed"
        elif step.get("status") != "failed":
            step["status"] = "verified" if verified else "executed"


def summarize_plan(plan: dict[str, Any]) -> dict[str, Any]:
    steps = [step for step in plan.get("steps", []) if isinstance(step, dict)]
    planned = [step for step in steps if step.get("tools")]
    unused = [str(step.get("title")) for step in planned if not step.get("executed")]
    failed = [str(step.get("title")) for step in steps if step.get("status") == "failed"]
    return {
        "steps": len(steps),
        "tool_steps": len(planned),
        "executed_steps": len([step for step in planned if step.get("executed")]),
        "unused_steps": unused,
        "failed_steps": failed,
    }


async def create_plan(
    provider: LLMProvider,
    instruction: str,
    tool_names: list[str],
    memory_context: str = "",
) -> dict[str, Any]:
    fallback = {
        "objective": instruction.strip()[:300],
        "steps": [{"title": "Execute the request with the appropriate tools", "tools": []}],
        "source": "fallback",
    }
    try:
        response = await provider.chat(
            [
                ChatMessage(role="system", content=PLANNER_PROMPT),
                ChatMessage(
                    role="user",
                    content=(
                        f"Objective:\n{instruction}\n\n"
                        f"Available tools:\n{', '.join(tool_names)}\n\n"
                        f"Relevant memory:\n{memory_context or '(none)'}"
                    ),
                ),
            ],
            temperature=0.1,
            # Planning is structured extraction, not open-ended reasoning. With
            # qwen3:4b, thinking made this call take 33-113s and sometimes emit
            # malformed JSON; JSON mode without thinking returns a valid plan in
            # ~3s. Agent turns keep their configured thinking behaviour.
            think=False,
            json_mode=True,
        )
        data = _extract_json(response.content)
        if not data or not isinstance(data.get("steps"), list):
            return validate_plan(fallback, instruction, tool_names)[0]
        steps = []
        for index, step in enumerate(data["steps"][:8]):
            if isinstance(step, str):
                steps.append({"title": step[:200], "tools": []})
            elif isinstance(step, dict):
                steps.append(
                    {
                        "title": str(step.get("title") or step.get("step") or f"Step {index + 1}")[:200],
                        "tools": [name for name in step.get("tools", []) if name in tool_names][:6],
                    }
                )
        if not steps:
            return validate_plan(fallback, instruction, tool_names)[0]
        plan = {
            "objective": str(data.get("objective") or instruction)[:300],
            "steps": steps,
            "source": "llm",
        }
        return validate_plan(plan, instruction, tool_names)[0]
    except Exception as exc:
        log.info("Planner unavailable, using fallback plan: %s", exc)
        return validate_plan(fallback, instruction, tool_names)[0]


def _extract_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    block = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidate = block.group(1) if block else text
    if not block:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start == -1 or end == -1:
            return None
        candidate = candidate[start : end + 1]
    try:
        loaded = json.loads(candidate)
        return loaded if isinstance(loaded, dict) else None
    except json.JSONDecodeError:
        pass
    # Small local models sometimes emit one extra closing brace or trailing
    # prose. Parse the first complete JSON object and ignore the remainder.
    start = candidate.find("{")
    if start == -1:
        return None
    try:
        loaded, _end = json.JSONDecoder().raw_decode(candidate[start:])
    except json.JSONDecodeError:
        return None
    return loaded if isinstance(loaded, dict) else None
