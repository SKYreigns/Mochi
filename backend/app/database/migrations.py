"""Idempotent SQLite schema upgrades.

The project intentionally has no migration framework: ``Base.metadata.create_all``
creates new tables, and this module adds columns to databases that predate the
persistent execution history. Every step is safe to run on every startup.
"""

from __future__ import annotations

import json

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

from app.core.logging import get_logger

log = get_logger("database.migrations")

_TOOL_EXECUTION_COLUMNS: dict[str, str] = {
    "conversation_id": "VARCHAR(40)",
    "status": "VARCHAR(20)",
    "verified": "BOOLEAN",
    "evidence": "TEXT",
    "hint": "TEXT",
    "approval_status": "VARCHAR(20)",
    "started_at": "DATETIME",
    "completed_at": "DATETIME",
}

_MESSAGE_COLUMNS: dict[str, str] = {"task_id": "VARCHAR(40)"}


def _columns(connection: Connection, table: str) -> set[str]:
    return {column["name"] for column in inspect(connection).get_columns(table)}


def _add_missing(connection: Connection, table: str, columns: dict[str, str]) -> list[str]:
    existing = _columns(connection, table)
    added: list[str] = []
    for name, ddl in columns.items():
        if name in existing:
            continue
        connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
        added.append(name)
    return added


def apply_schema_migrations(connection: Connection) -> None:
    """Upgrade a pre-existing database in place. Never drops data."""

    added = _add_missing(connection, "tool_executions", _TOOL_EXECUTION_COLUMNS)
    added += _add_missing(connection, "messages", _MESSAGE_COLUMNS)

    # Backfill executions created before the history columns existed.
    connection.execute(
        text(
            """
            UPDATE tool_executions
               SET conversation_id = (
                   SELECT tasks.conversation_id FROM tasks WHERE tasks.id = tool_executions.task_id
               )
             WHERE conversation_id IS NULL
            """
        )
    )
    connection.execute(
        text(
            """
            UPDATE tool_executions
               SET status = CASE WHEN ok = 1 THEN 'completed' ELSE 'failed' END
             WHERE status IS NULL OR status = ''
            """
        )
    )
    connection.execute(
        text("UPDATE tool_executions SET started_at = created_at WHERE started_at IS NULL")
    )
    connection.execute(
        text("UPDATE tool_executions SET completed_at = created_at WHERE completed_at IS NULL")
    )
    connection.execute(
        text("UPDATE tool_executions SET verified = 0 WHERE verified IS NULL")
    )

    # ``verified`` lives inside the serialized ToolResult for old rows.
    rows = connection.execute(
        text("SELECT id, result FROM tool_executions WHERE verified = 0 AND result IS NOT NULL")
    ).fetchall()
    for row in rows:
        payload = row[1]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                continue
        if isinstance(payload, dict) and payload.get("verified") is True:
            connection.execute(
                text("UPDATE tool_executions SET verified = 1 WHERE id = :id"), {"id": row[0]}
            )

    for statement in (
        "CREATE INDEX IF NOT EXISTS ix_tool_executions_conversation_id ON tool_executions (conversation_id)",
        "CREATE INDEX IF NOT EXISTS ix_tool_executions_status ON tool_executions (status)",
        "CREATE INDEX IF NOT EXISTS ix_messages_task_id ON messages (task_id)",
    ):
        connection.execute(text(statement))

    if added:
        log.info("Schema upgraded; added columns: %s", ", ".join(sorted(added)))
