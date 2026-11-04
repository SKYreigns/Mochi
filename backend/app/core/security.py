"""Security primitives: filesystem sandbox, secret detection, shell policy."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.core.errors import PolicyViolationError

_PROTECTED_ROOTS = [
    Path(os.environ.get("SystemRoot", r"C:\Windows")),
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
    Path(os.environ.get("ProgramData", r"C:\ProgramData")),
]

# Files that must never be read by tools and fed to an LLM.
_SENSITIVE_NAMES = {
    ".env",
    ".env.local",
    "credentials",
    "credentials.json",
    "secrets.json",
    "id_rsa",
    "id_ed25519",
    "ntds.dit",
    "jarvis.db",
    "cookies.sqlite",
    "login data",
}
_SENSITIVE_SUFFIXES = {".pem", ".key", ".pfx", ".p12", ".kdbx"}

_WRITE_EXTENSIONS_ALLOWED = {
    ".txt", ".md", ".json", ".csv", ".yaml", ".yml", ".log", ".html", ".htm",
    ".pdf", ".docx", ".xlsx", ".pptx", ".png", ".jpg", ".jpeg", ".webp",
    ".py", ".ts", ".tsx", ".js", ".jsx", ".css", ".sql", ".xml", ".zip",
}

_SECRET_CONTENT_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|secret|password|passwd)\s*[:=]\s*\S{6,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{20,}"),
]


def is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def contains_secret(text: str) -> bool:
    return any(pattern.search(text) for pattern in _SECRET_CONTENT_PATTERNS)


def is_sensitive_file(path: Path) -> bool:
    lowered = path.name.lower()
    if lowered in _SENSITIVE_NAMES:
        return True
    if path.suffix.lower() in _SENSITIVE_SUFFIXES:
        return True
    parts = {part.lower() for part in path.parts}
    if "browser" in parts and "profile" in parts:
        return True
    return False


class PathPolicy:
    """Enforces the allowed-directory sandbox for every filesystem operation."""

    def __init__(self, allowed_directories: list[Path], workspace: Path):
        self.workspace = workspace.resolve()
        self._allowed: list[Path] = []
        self.set_allowed(allowed_directories)

    def set_allowed(self, directories: list[Path]) -> None:
        clean: list[Path] = []
        for directory in directories:
            resolved = Path(directory).expanduser().resolve()
            if any(is_within(resolved, protected) for protected in _PROTECTED_ROOTS):
                continue
            clean.append(resolved)
        self._allowed = clean

    @property
    def allowed(self) -> list[Path]:
        return list(self._allowed)

    def resolve(self, raw: str | Path) -> Path:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = self.workspace / candidate
        return candidate.resolve(strict=False)

    def validate(self, raw: str | Path, *, must_exist: bool = False, check_sensitive: bool = True) -> Path:
        path = self.resolve(raw)
        if check_sensitive and is_sensitive_file(path):
            raise PolicyViolationError(
                f"Access to sensitive file '{path.name}' is blocked.",
                hint="Credential and session stores are intentionally inaccessible to the agent.",
            )
        if not any(is_within(path, root) for root in self._allowed):
            allowed = ", ".join(str(p) for p in self._allowed) or "(none configured)"
            raise PolicyViolationError(
                f"Path '{path}' is outside the allowed directories.",
                hint=f"Add the directory in Settings. Allowed roots: {allowed}",
            )
        if must_exist and not path.exists():
            raise PolicyViolationError(f"Path '{path}' does not exist.")
        return path


@dataclass(slots=True)
class ShellPolicyResult:
    readonly: bool
    reason: str
    blocked: bool = False
    details: dict[str, str] = field(default_factory=dict)


# Read-only PowerShell cmdlets that may run without an approval prompt.
_ALLOWED_READONLY = {
    "get-childitem", "get-item", "get-process",
    "get-service", "get-ciminstance", "get-wmiobject", "get-volume", "get-disk",
    "get-physicaldisk", "get-computerinfo", "get-date", "get-location",
    "get-command", "get-module", "get-package", "get-appxpackage", "test-path",
    "resolve-path", "select-object", "where-object", "sort-object", "measure-object",
    "format-table", "format-list", "out-string", "convertto-json", "write-output",
}

_BLOCKED_COMMANDS = (
    "remove-item", "rm ", "del ", "rmdir", "format-volume", "clear-disk",
    "initialize-disk", "stop-computer", "restart-computer", "shutdown",
    "set-executionpolicy", "new-localuser", "net user", "net localgroup",
    "reg add", "reg delete", "reg import", "schtasks /create", "schtasks /delete",
    "invoke-expression", "iex ", "invoke-webrequest", "curl ", "wget ",
    "start-process", "start-job", "new-service", "sc.exe", "takeown", "icacls",
    "cipher /w", "attrib ", "bcdedit", "diskpart", "mount-diskimage",
    "enable-psremoting", "set-mppreference", "add-mppreference",
)


def validate_powershell(command: str) -> ShellPolicyResult:
    """Evaluate a PowerShell command against the explicit command policy.

    Returns readonly=True only when every pipeline segment uses an allowed
    read-only cmdlet. Dangerous constructs are blocked outright. Anything else
    is permitted only through the approval flow.
    """
    normalized = command.strip().lower()
    if not normalized:
        return ShellPolicyResult(False, "Empty command", blocked=True)

    for token in ("`", "$(", "&&", "||", ";", ">", "<", "| iex", "invoke-expression"):
        if token in normalized:
            return ShellPolicyResult(False, f"Blocked shell construct: {token}", blocked=True)

    for banned in _BLOCKED_COMMANDS:
        if normalized.startswith(banned) or f" {banned}" in normalized or f"|{banned}" in normalized:
            return ShellPolicyResult(False, f"Command is on the blocked list: {banned.strip()}", blocked=True)

    segments = [segment.strip() for segment in normalized.split("|") if segment.strip()]
    if not segments:
        return ShellPolicyResult(False, "Empty pipeline", blocked=True)

    for index, segment in enumerate(segments):
        first = segment.split()[0]
        if index == 0:
            if first.startswith("$") or first.startswith("("):
                return ShellPolicyResult(False, "Variable/expression commands require approval", blocked=True)
            if first not in _ALLOWED_READONLY:
                return ShellPolicyResult(False, f"'{first}' is not a read-only allowlisted cmdlet")
        else:
            if first not in _ALLOWED_READONLY:
                return ShellPolicyResult(False, f"Pipeline stage '{first}' is not allowlisted")
    return ShellPolicyResult(True, "Read-only allowlisted pipeline")
