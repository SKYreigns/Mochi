"""Shared enums and constants."""

from __future__ import annotations

from enum import StrEnum


class PermissionMode(StrEnum):
    OBSERVE = "observe"
    ASSISTED = "assisted"
    AUTONOMOUS = "autonomous"


class ToolPermission(StrEnum):
    READ = "read"
    NAVIGATE = "navigate"
    WRITE = "write"
    DESTRUCTIVE = "destructive"
    ADMIN = "admin"
    SENSITIVE = "sensitive"


class TaskStatus(StrEnum):
    QUEUED = "queued"
    PLANNING = "planning"
    RUNNING = "running"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class MemoryKind(StrEnum):
    PREFERENCE = "preference"
    FACT = "fact"
    WORKFLOW = "workflow"
    WEBSITE = "website"
    INSTRUCTION = "instruction"


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class TriggerKind(StrEnum):
    MANUAL = "manual"
    SCHEDULE = "schedule"
    WORKFLOW = "workflow"
