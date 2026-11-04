"""LLM-backed task planner with a safe deterministic fallback."""

from __future__ import annotations

import json
import re
from typing import Any

from app.core.logging import get_logger
from app.providers.base import ChatMessage, LLMProvider
from app.orchestrator.prompts import PLANNER_PROMPT

log = get_logger("planner")


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
        )
        data = _extract_json(response.content)
        if not data or not isinstance(data.get("steps"), list):
            return fallback
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
            return fallback
        return {
            "objective": str(data.get("objective") or instruction)[:300],
            "steps": steps,
            "source": "llm",
        }
    except Exception as exc:
        log.info("Planner unavailable, using fallback plan: %s", exc)
        return fallback


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
        return None
