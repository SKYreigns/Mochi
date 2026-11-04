"""The tool registry must expose valid, uniquely named, JSON-schema-able tools."""

from __future__ import annotations

from app.core.enums import ToolPermission


def test_registry_has_expected_coverage(services):
    tools = services.registry.all()
    assert len(tools) >= 70
    names = {tool.name for tool in tools}
    for prefix in ("browser.", "desktop.", "files.", "documents.", "system.", "lms.", "memory."):
        assert any(name.startswith(prefix) for name in names), f"missing {prefix} tools"


def test_every_tool_has_a_valid_schema(services):
    for tool in services.registry.all():
        schema = tool.schema()
        assert schema.get("type") == "object"
        assert isinstance(schema.get("properties", {}), dict)
        spec = tool.spec()
        assert spec["type"] == "function"
        assert spec["function"]["name"] == tool.name
        assert spec["function"]["description"].strip()


def test_tool_names_are_unique(services):
    names = services.registry.names()
    assert len(names) == len(set(names))


def test_permissions_are_declared(services):
    for tool in services.registry.all():
        assert isinstance(tool.permission, ToolPermission)
        assert tool.timeout > 0
        if tool.requires_approval:
            assert tool.approval_reason


async def test_unknown_tool_cannot_execute(services, tool_context):
    result, duration = await services.registry.execute("does.not.exist", {}, tool_context)
    assert result.ok is False
    assert "Unknown tool" in (result.error or "")
    assert duration == 0.0


async def test_invalid_arguments_are_rejected_before_execution(services, tool_context):
    result, _duration = await services.registry.execute(
        "files.read_file", {"path": 123, "unexpected": "x"}, tool_context
    )
    assert result.ok is False
    assert "Invalid arguments" in (result.error or "")

