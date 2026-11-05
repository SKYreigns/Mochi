"""Measure real Ollama latency, time-to-first-token and tool-selection reliability.

Usage (from ``backend``)::

    .\\.venv\\Scripts\\python.exe scripts\\benchmark_ollama.py --runs 3

No database, no paid API and no model download: it talks to the configured local
Ollama endpoint only.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.providers.base import ChatMessage  # noqa: E402
from app.providers.ollama import OllamaProvider  # noqa: E402
from app.tools.base import ToolRegistry  # noqa: E402
from app.tools.system.tools import register_system_tools  # noqa: E402

TOOLS = ("system.get_cpu_usage", "system.get_ram_usage")
PROMPT = "Check my CPU usage and memory usage, then summarize both."


def tool_specs() -> list[dict[str, Any]]:
    registry = ToolRegistry()
    register_system_tools(registry)
    return [spec for spec in registry.specs() if spec["function"]["name"] in TOOLS]


async def unload(base_url: str, model: str) -> None:
    async with httpx.AsyncClient(timeout=60.0) as client:
        await client.post(
            f"{base_url}/api/generate",
            json={"model": model, "prompt": "", "keep_alive": 0},
        )


async def loaded_models(base_url: str) -> list[str]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(f"{base_url}/api/ps")
        response.raise_for_status()
        return [item.get("name", "") for item in response.json().get("models", [])]


async def one_run(
    provider: OllamaProvider,
    messages: list[ChatMessage],
    tools: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    started = time.perf_counter()
    response = await provider.chat(messages, tools=tools)
    wall_ms = (time.perf_counter() - started) * 1000
    usage = response.usage
    return {
        "wall_ms": round(wall_ms, 1),
        "first_token_ms": usage.get("first_token_ms"),
        "total_ms": usage.get("total_ms"),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "tokens_per_second": usage.get("tokens_per_second"),
        "load_duration_ms": usage.get("load_duration_ms"),
        "eval_duration_ms": usage.get("eval_duration_ms"),
        "tool_calls": [call.name for call in response.tool_calls],
        "tool_selection_safe": bool(response.tool_calls)
        and all(call.name in TOOLS for call in response.tool_calls),
        "content_chars": len(response.content),
    }


def summarize(runs: list[dict[str, Any]]) -> dict[str, Any]:
    def values(key: str) -> list[float]:
        return [run[key] for run in runs if isinstance(run.get(key), (int, float))]

    summary: dict[str, Any] = {"runs": len(runs), "raw": runs}
    for key in ("wall_ms", "first_token_ms", "total_ms", "tokens_per_second"):
        data = values(key)
        if data:
            summary[key] = {
                "median": round(statistics.median(data), 1),
                "min": round(min(data), 1),
                "max": round(max(data), 1),
            }
    summary["tool_selection_correct"] = sum(1 for run in runs if run["tool_selection_safe"])
    return summary


async def benchmark_config(
    base_url: str,
    model: str,
    think: bool | None,
    runs: int,
    *,
    cold: bool,
) -> dict[str, Any]:
    provider = OllamaProvider(base_url=base_url, model=model, think=think, keep_alive="10m")
    label = "default" if think is None else ("on" if think else "off")
    result: dict[str, Any] = {"think": label, "cold": cold}
    try:
        simple = [ChatMessage(role="user", content="Reply with one short sentence: ready.")]
        if cold:
            await unload(base_url, model)
            for _ in range(60):
                if model not in await loaded_models(base_url):
                    break
                await asyncio.sleep(0.5)
        first = await one_run(provider, simple, None)
        result["cold_start"] = first
        warm = [await one_run(provider, simple, None) for _ in range(runs)]
        result["warm"] = summarize(warm)

        tool_messages = [
            ChatMessage(role="system", content="Select the tools needed for the request."),
            ChatMessage(role="user", content=PROMPT),
        ]
        tool_runs = [await one_run(provider, tool_messages, tool_specs()) for _ in range(runs)]
        result["tool_selection"] = summarize(tool_runs)
    finally:
        await provider.aclose()
    return result


def markdown(results: list[dict[str, Any]]) -> str:
    lines = [
        "| configuration | scenario | median wall ms | median TTFT ms | median tok/s | tool selection |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for item in results:
        think = item["think"]
        cold = item.get("cold_start") or {}
        warm = item.get("warm") or {}
        tools = item.get("tool_selection") or {}
        lines.append(
            f"| think={think} | cold start | {cold.get('wall_ms', '-')} | {cold.get('first_token_ms', '-')} | {cold.get('tokens_per_second', '-')} | - |"
        )
        lines.append(
            f"| think={think} | warm chat | {warm.get('wall_ms', {}).get('median', '-')} | {warm.get('first_token_ms', {}).get('median', '-')} | {warm.get('tokens_per_second', {}).get('median', '-')} | - |"
        )
        lines.append(
            f"| think={think} | two-tool selection | {tools.get('wall_ms', {}).get('median', '-')} | {tools.get('first_token_ms', {}).get('median', '-')} | {tools.get('tokens_per_second', {}).get('median', '-')} | {tools.get('tool_selection_correct', 0)}/{tools.get('runs', 0)} |"
        )
    return "\n".join(lines)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--model", default="qwen3:4b")
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--cold", action="store_true", help="Unload the model before the first request")
    parser.add_argument("--out", default="", help="Write raw JSON results to this path")
    args = parser.parse_args()

    results = []
    for think in (None, False):
        results.append(
            await benchmark_config(
                args.base_url,
                args.model,
                think,
                args.runs,
                cold=args.cold,
            )
        )

    table = markdown(results)
    print(table)
    payload = {"model": args.model, "base_url": args.base_url, "results": results}
    if args.out:
        Path(args.out).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
