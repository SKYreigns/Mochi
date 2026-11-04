"""Typed application errors with safe, user-facing messages."""

from __future__ import annotations

from typing import Any


class JarvisError(Exception):
    code = "jarvis_error"
    retryable = False

    def __init__(self, message: str, *, hint: str = "", details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "hint": self.hint,
            "retryable": self.retryable,
            "details": self.details,
        }


class ConfigurationError(JarvisError):
    code = "configuration_error"


class ProviderError(JarvisError):
    code = "provider_error"
    retryable = True


class ToolNotFoundError(JarvisError):
    code = "tool_not_found"


class ToolValidationError(JarvisError):
    code = "tool_validation_error"


class ToolExecutionError(JarvisError):
    code = "tool_execution_error"
    retryable = True


class PermissionDeniedError(JarvisError):
    code = "permission_denied"


class PolicyViolationError(JarvisError):
    code = "policy_violation"


class ApprovalTimeoutError(JarvisError):
    code = "approval_timeout"


class TaskCancelledError(JarvisError):
    code = "task_cancelled"


class EmergencyStopError(JarvisError):
    code = "emergency_stop"


class NotFoundError(JarvisError):
    code = "not_found"


class BudgetExceededError(JarvisError):
    code = "budget_exceeded"


class LoginRequiredError(JarvisError):
    code = "login_required"

    def __init__(self, message: str, *, hint: str = "Complete the login manually in the browser window, then resume the task."):
        super().__init__(message, hint=hint)
