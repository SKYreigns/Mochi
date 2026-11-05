"""Ollama wire-format regression tests (no network calls)."""

from __future__ import annotations

import json

import httpx

from app.core.config import Settings
from app.providers.base import ChatMessage, ToolCall
from app.providers.manager import ProviderManager
from app.providers.ollama import OllamaProvider
from app.services.settings_service import SettingsService


async def test_tool_result_is_associated_with_tool_name() -> None:
    provider = OllamaProvider(base_url="http://127.0.0.1:11434", model="qwen3:4b")
    try:
        payload = provider._message_payload(
            ChatMessage(role="tool", content='{"ok": true}', name="system.get_cpu_usage")
        )
    finally:
        await provider.aclose()

    assert payload["role"] == "tool"
    assert payload["tool_name"] == "system.get_cpu_usage"
    assert payload["content"] == '{"ok": true}'


async def test_assistant_tool_calls_are_serialized() -> None:
    provider = OllamaProvider(base_url="http://127.0.0.1:11434", model="qwen3:4b")
    try:
        payload = provider._message_payload(
            ChatMessage(
                role="assistant",
                content="",
                tool_calls=[ToolCall(id="call_1", name="system.get_cpu_usage", arguments={})],
            )
        )
    finally:
        await provider.aclose()

    assert payload["tool_calls"] == [
        {"function": {"name": "system.get_cpu_usage", "arguments": {}}}
    ]


def test_payload_omits_thinking_unless_configured() -> None:
    provider = OllamaProvider(base_url="http://127.0.0.1:11434", model="qwen3:4b")
    payload = provider._request_payload([ChatMessage(role="user", content="hi")], None, 0.2)

    # The model default is preserved: thinking is never disabled implicitly.
    assert "think" not in payload
    assert "keep_alive" not in payload
    assert payload["options"] == {"temperature": 0.2}


def test_payload_carries_explicit_inference_options() -> None:
    provider = OllamaProvider(
        base_url="http://127.0.0.1:11434",
        model="qwen3:4b",
        think=False,
        keep_alive="30m",
        num_ctx=8192,
    )
    payload = provider._request_payload([ChatMessage(role="user", content="hi")], None, 0.1)

    assert payload["think"] is False
    assert payload["keep_alive"] == "30m"
    assert payload["options"] == {"temperature": 0.1, "num_ctx": 8192}


def test_per_call_thinking_and_json_mode_override_config() -> None:
    provider = OllamaProvider(
        base_url="http://127.0.0.1:11434", model="qwen3:4b", think=True
    )
    payload = provider._request_payload(
        [ChatMessage(role="user", content="hi")],
        None,
        0.1,
        think=False,
        json_mode=True,
    )

    assert payload["think"] is False
    assert payload["format"] == "json"


async def test_stream_parses_timing_metrics_and_requests_options() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        body = "\n".join(
            [
                json.dumps({"message": {"content": "", "thinking": "considering"}, "done": False}),
                json.dumps({"message": {"content": "Hello"}, "done": False}),
                json.dumps(
                    {
                        "message": {"content": ""},
                        "done": True,
                        "prompt_eval_count": 10,
                        "eval_count": 5,
                        "prompt_eval_duration": 100_000_000,
                        "eval_duration": 500_000_000,
                        "load_duration": 50_000_000,
                        "total_duration": 700_000_000,
                    }
                ),
            ]
        )
        return httpx.Response(200, text=body)

    provider = OllamaProvider(
        base_url="http://127.0.0.1:11434",
        model="qwen3:4b",
        think=False,
        keep_alive="10m",
        num_ctx=4096,
    )
    await provider._client.aclose()
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    streamed: list[str] = []
    try:
        response = await provider.chat(
            [ChatMessage(role="user", content="hi")],
            on_text=lambda chunk: _collect(streamed, chunk),
        )
    finally:
        await provider.aclose()

    assert response.content == "Hello"
    assert streamed == ["Hello"]
    assert seen["think"] is False
    assert seen["keep_alive"] == "10m"
    assert seen["options"] == {"temperature": 0.2, "num_ctx": 4096}

    usage = response.usage
    assert usage["prompt_tokens"] == 10
    assert usage["completion_tokens"] == 5
    assert usage["tokens_per_second"] == 10.0
    assert usage["eval_duration_ms"] == 500.0
    assert usage["total_duration_ms"] == 700.0
    assert usage["first_token_ms"] is not None


async def _collect(bucket: list[str], chunk: str) -> None:
    bucket.append(chunk)


async def test_manager_reads_inference_settings(tmp_path):
    settings = Settings(
        environment="test",
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'mgr.db').as_posix()}",
        llm_provider="ollama",
        llm_model="qwen3:4b",
        llm_think=False,
        llm_keep_alive="20m",
        llm_num_ctx=8192,
    )
    runtime = SettingsService(settings, events=_NoopEvents())  # type: ignore[arg-type]
    manager = ProviderManager(settings, runtime)
    provider = await manager.get()
    try:
        assert isinstance(provider, OllamaProvider)
        assert provider.think is False
        assert provider.keep_alive == "20m"
        assert provider.num_ctx == 8192
    finally:
        await manager.aclose()


class _NoopEvents:
    async def publish(self, *_args, **_kwargs) -> None:
        return None
