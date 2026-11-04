"""WebSocket event stream over a real uvicorn server."""

from __future__ import annotations

import asyncio
import json

import pytest
import uvicorn
import websockets

from app.main import create_app


@pytest.fixture
async def live_server(services):
    app = create_app(services.app_settings, services=services)
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="on")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve(), name="test-uvicorn")
    for _ in range(300):
        if server.started:
            break
        await asyncio.sleep(0.03)
    else:
        server.should_exit = True
        await task
        raise RuntimeError("uvicorn test server did not start")
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"127.0.0.1:{port}"
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(task, timeout=15)
        except asyncio.TimeoutError:  # pragma: no cover
            task.cancel()


async def _drain(ws, timeout: float = 0.5) -> list[dict]:
    events = []
    while True:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        except asyncio.TimeoutError:
            return events
        events.append(json.loads(raw))


async def _receive_until(ws, event_type: str, timeout: float = 15.0, predicate=None) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError(f"event {event_type} not received")
        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        event = json.loads(raw)
        if event.get("type") == event_type and (predicate is None or predicate(event)):
            return event


async def test_connection_and_hello(live_server, services):
    async with websockets.connect(f"ws://{live_server}/ws/events") as ws:
        hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        assert hello["type"] == "hello"
        assert hello["payload"]["tools"] >= 70
        assert services.events.subscriber_count == 1
        await _drain(ws, timeout=0.3)
    for _ in range(100):
        if services.events.subscriber_count == 0:
            break
        await asyncio.sleep(0.03)
    assert services.events.subscriber_count == 0


async def test_event_delivery_and_multiple_types(live_server, services):
    async with websockets.connect(f"ws://{live_server}/ws/events") as ws:
        await asyncio.wait_for(ws.recv(), timeout=10)  # hello
        await _drain(ws, timeout=0.3)

        await services.events.publish("test.alpha", {"value": 1})
        await services.events.publish("test.beta", {"value": 2})
        await services.events.publish("test.gamma", {"value": 3})

        seen = {}
        while len(seen) < 3:
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
            event = json.loads(raw)
            if event["type"].startswith("test."):
                seen[event["type"]] = event["payload"]["value"]

        assert seen == {"test.alpha": 1, "test.beta": 2, "test.gamma": 3}


async def test_invalid_client_message_does_not_break_stream(live_server, services):
    async with websockets.connect(f"ws://{live_server}/ws/events") as ws:
        await asyncio.wait_for(ws.recv(), timeout=10)
        await _drain(ws, timeout=0.2)
        await ws.send("this is not json")
        await ws.send(json.dumps({"unexpected": True}))
        await services.events.publish("test.after-garbage", {"ok": True})
        event = await _receive_until(ws, "test.after-garbage")
        assert event["payload"]["ok"] is True
        await services.events.publish("test.still-open", {})
        await _receive_until(ws, "test.still-open")


async def test_reconnection(live_server, services):
    async with websockets.connect(f"ws://{live_server}/ws/events") as ws:
        assert json.loads(await asyncio.wait_for(ws.recv(), timeout=10))["type"] == "hello"
    async with websockets.connect(f"ws://{live_server}/ws/events") as ws:
        assert json.loads(await asyncio.wait_for(ws.recv(), timeout=10))["type"] == "hello"
        await services.events.publish("test.reconnected", {})
        await _receive_until(ws, "test.reconnected")


async def test_task_progress_events(live_server, services):
    import httpx

    async with websockets.connect(f"ws://{live_server}/ws/events", max_size=4 * 1024 * 1024) as ws:
        await asyncio.wait_for(ws.recv(), timeout=10)  # hello
        await _drain(ws, timeout=0.3)

        async with httpx.AsyncClient(base_url=f"http://{live_server}", timeout=30) as client:
            response = await client.post("/api/chat", json={"message": "Say hello"})
        assert response.status_code == 200
        task_id = response.json()["task_id"]

        seen = set()
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=30)
            event = json.loads(raw)
            payload = event.get("payload", {})
            if payload.get("task_id") != task_id:
                continue
            seen.add(event["type"])
            if event["type"] in ("task.completed", "task.failed", "task.cancelled"):
                break

        assert "task.queued" in seen or "task.status" in seen
        assert "task.plan" in seen
        assert "task.completed" in seen
        assert "llm.completed" in seen
