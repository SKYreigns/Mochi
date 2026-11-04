"""Filesystem tools: sandboxing, reversible deletion and invalid input handling."""

from __future__ import annotations

from pathlib import Path

from app.core.security import PathPolicy


async def _run(services, ctx, name, arguments):
    result, _duration = await services.registry.execute(name, arguments, ctx)
    return result


async def test_create_read_and_list(services, tool_context, sandbox):
    target = sandbox / "notes" / "todo.txt"
    created = await _run(services, tool_context, "files.create_file", {"path": str(target), "content": "alpha\nbeta"})
    assert created.ok and target.exists()
    assert "alpha" in target.read_text(encoding="utf-8")

    read = await _run(services, tool_context, "files.read_file", {"path": str(target)})
    assert read.ok and read.data["content"].splitlines() == ["alpha", "beta"]

    listing = await _run(services, tool_context, "files.list_directory", {"path": str(sandbox)})
    assert listing.ok
    names = {entry["name"] for entry in listing.data["entries"]}
    assert "notes" in names


async def test_create_file_requires_overwrite_flag(services, tool_context, sandbox):
    target = sandbox / "existing.txt"
    target.write_text("original", encoding="utf-8")
    refused = await _run(services, tool_context, "files.create_file", {"path": str(target), "content": "new"})
    assert refused.ok is False
    assert "already exists" in refused.error
    assert target.read_text(encoding="utf-8") == "original"

    replaced = await _run(
        services, tool_context, "files.create_file", {"path": str(target), "content": "new", "overwrite": True}
    )
    assert replaced.ok and target.read_text(encoding="utf-8") == "new"
    assert replaced.data["backup"], "overwrite must create a recoverable backup"


async def test_copy_move_and_rename(services, tool_context, sandbox):
    original = sandbox / "source.txt"
    original.write_text("payload", encoding="utf-8")

    copied = await _run(
        services,
        tool_context,
        "files.copy",
        {"source": str(original), "destination": str(sandbox / "copy.txt")},
    )
    assert copied.ok and (sandbox / "copy.txt").read_text(encoding="utf-8") == "payload"

    moved = await _run(
        services,
        tool_context,
        "files.move",
        {"source": str(sandbox / "copy.txt"), "destination": str(sandbox / "moved.txt")},
    )
    assert moved.ok and not (sandbox / "copy.txt").exists()

    renamed = await _run(
        services,
        tool_context,
        "files.rename",
        {"source": str(sandbox / "moved.txt"), "destination": str(sandbox / "renamed.txt")},
    )
    assert renamed.ok and (sandbox / "renamed.txt").exists()


async def test_sandbox_rejects_paths_outside_allowed_roots(services, tool_context, sandbox, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")

    result = await _run(services, tool_context, "files.read_file", {"path": str(outside)})
    assert result.ok is False
    assert "outside the allowed directories" in (result.error or "")

    traversal = await _run(
        services, tool_context, "files.read_file", {"path": str(sandbox / ".." / "outside.txt")}
    )
    assert traversal.ok is False
    assert "outside the allowed directories" in (traversal.error or "")


async def test_protected_sensitive_files_are_blocked(services, tool_context, sandbox):
    env_file = sandbox / ".env"
    env_file.write_text("API_KEY=super-secret", encoding="utf-8")
    result = await _run(services, tool_context, "files.read_file", {"path": str(env_file)})
    assert result.ok is False
    assert "sensitive" in (result.error or "").lower()

    # System directories are never added to the sandbox.
    policy = PathPolicy([Path("C:/Windows"), Path("C:/Program Files")], workspace=sandbox)
    assert policy.allowed == []


async def test_delete_is_recoverable_and_refuses_roots(services, tool_context, sandbox):
    target = sandbox / "trash-me.txt"
    target.write_text("recoverable", encoding="utf-8")
    deleted = await _run(services, tool_context, "files.delete", {"path": str(target)})
    assert deleted.ok
    assert not target.exists()
    assert deleted.data["permanent"] is False
    recovered = Path(deleted.data["recoverable_at"])
    assert recovered.exists() and recovered.read_text(encoding="utf-8") == "recoverable"
    assert recovered.is_relative_to((services.app_settings.data_dir / "trash").resolve())

    root_delete = await _run(services, tool_context, "files.delete", {"path": str(sandbox)})
    assert root_delete.ok is False
    assert "allowed root" in (root_delete.error or "")


async def test_non_empty_directory_requires_recursive(services, tool_context, sandbox):
    folder = sandbox / "folder"
    folder.mkdir()
    (folder / "file.txt").write_text("x", encoding="utf-8")

    refused = await _run(services, tool_context, "files.delete", {"path": str(folder)})
    assert refused.ok is False
    assert "not empty" in (refused.error or "")

    removed = await _run(services, tool_context, "files.delete", {"path": str(folder), "recursive": True})
    assert removed.ok and not folder.exists()


async def test_invalid_paths_return_actionable_errors(services, tool_context, sandbox):
    missing = await _run(services, tool_context, "files.read_file", {"path": str(sandbox / "missing.txt")})
    assert missing.ok is False
    assert "does not exist" in (missing.error or "")

    not_dir = await _run(services, tool_context, "files.list_directory", {"path": str(sandbox / "missing")})
    assert not_dir.ok is False


async def test_relative_paths_resolve_inside_workspace(services, tool_context):
    result = await _run(
        services, tool_context, "files.create_file", {"path": "relative-test.txt", "content": "workspace"}
    )
    assert result.ok
    assert Path(result.data["path"]).is_relative_to((services.app_settings.data_dir / "workspace").resolve())
