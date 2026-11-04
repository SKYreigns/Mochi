"""Filesystem tools: sandboxed, reversible where practical, previewed for bulk ops."""

from __future__ import annotations

import hashlib
import json
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.core.enums import ToolPermission
from app.core.errors import PolicyViolationError
from app.core.security import is_within
from app.tools.base import EmptyArgs, ToolContext, ToolRegistry, build_tool, fail, ok

MAX_READ_BYTES = 2_000_000
MAX_SEARCH_RESULTS = 250

_CATEGORY_MAP: dict[str, set[str]] = {
    "Documents": {".pdf", ".doc", ".docx", ".txt", ".md", ".rtf", ".odt", ".epub"},
    "Spreadsheets": {".xlsx", ".xls", ".csv", ".ods", ".tsv"},
    "Presentations": {".pptx", ".ppt", ".odp", ".key"},
    "Images": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".tiff", ".heic"},
    "Audio": {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac", ".wma", ".opus"},
    "Video": {".mp4", ".mkv", ".avi", ".mov", ".webm", ".wmv", ".flv"},
    "Archives": {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"},
    "Code": {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".cpp", ".cs", ".go", ".rs", ".html", ".css", ".sql", ".json", ".xml", ".yaml", ".yml"},
    "Installers": {".exe", ".msi", ".msix", ".appx", ".dmg", ".pkg"},
}


# --- schemas ---------------------------------------------------------------
class ListArgs(BaseModel):
    path: str = Field(description="Absolute directory path inside allowed roots.")
    show_hidden: bool = False


class SearchArgs(BaseModel):
    query: str = Field(description="Filename glob or substring, e.g. '*.pdf' or 'assignment'.")
    directory: str = Field(description="Directory to search recursively.")
    content_pattern: str | None = Field(default=None, description="Optional substring to search inside text files.")
    limit: int = Field(default=100, ge=1, le=MAX_SEARCH_RESULTS)


class ReadArgs(BaseModel):
    path: str
    max_chars: int = Field(default=20000, ge=100, le=200000)


class CreateFileArgs(BaseModel):
    path: str
    content: str = ""
    overwrite: bool = False


class CreateFolderArgs(BaseModel):
    path: str


class PathPairArgs(BaseModel):
    source: str
    destination: str
    overwrite: bool = False


class DeleteArgs(BaseModel):
    path: str
    recursive: bool = False


class PathArgs(BaseModel):
    path: str


class FindDuplicatesArgs(BaseModel):
    directory: str
    max_files: int = Field(default=3000, ge=1, le=20000)


class OrganizeArgs(BaseModel):
    directory: str
    mode: Literal["by_category", "by_extension", "by_subject"] = "by_category"
    subject_rules: dict[str, list[str]] = Field(
        default_factory=dict,
        description="For by_subject: folder name -> keywords matched case-insensitively in filenames.",
    )
    include_subfolders: bool = False
    dry_run: bool = True


def register_filesystem_tools(registry: ToolRegistry) -> None:
    async def allowed_roots(args: BaseModel, ctx: ToolContext):
        roots = [str(path) for path in ctx.services.path_policy.allowed]
        return ok({"roots": roots}, evidence=f"{len(roots)} allowed root(s)")

    async def list_directory(args: ListArgs, ctx: ToolContext):
        path = ctx.services.path_policy.validate(args.path, must_exist=True)
        if not path.is_dir():
            return fail(f"'{path}' is not a directory.")
        entries = []
        for entry in sorted(path.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
            if not args.show_hidden and entry.name.startswith("."):
                continue
            try:
                stat = entry.stat()
                entries.append(
                    {
                        "name": entry.name,
                        "path": str(entry),
                        "is_dir": entry.is_dir(),
                        "size": stat.st_size if entry.is_file() else None,
                        "modified": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                    }
                )
            except OSError:
                continue
            if len(entries) >= 500:
                break
        return ok({"path": str(path), "entries": entries, "count": len(entries)}, evidence=f"{len(entries)} entries in {path}")

    async def search(args: SearchArgs, ctx: ToolContext):
        root = ctx.services.path_policy.validate(args.directory, must_exist=True)
        if not root.is_dir():
            return fail(f"'{root}' is not a directory.")
        pattern = args.query if any(char in args.query for char in "*?[") else f"*{args.query}*"
        results: list[dict[str, Any]] = []
        scanned = 0
        for candidate in root.rglob(pattern):
            scanned += 1
            if scanned > 20000 or len(results) >= args.limit:
                break
            if not candidate.is_file():
                continue
            try:
                if not any(is_within(candidate, allowed) for allowed in ctx.services.path_policy.allowed):
                    continue
                from app.core.security import is_sensitive_file

                if is_sensitive_file(candidate):
                    continue
                if args.content_pattern and candidate.stat().st_size <= MAX_READ_BYTES:
                    try:
                        text = candidate.read_text(encoding="utf-8", errors="ignore")
                    except OSError:
                        continue
                    if args.content_pattern.lower() not in text.lower():
                        continue
                stat = candidate.stat()
                results.append(
                    {
                        "path": str(candidate),
                        "name": candidate.name,
                        "size": stat.st_size,
                        "modified": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                    }
                )
            except OSError:
                continue
        return ok(
            {"results": results, "count": len(results), "scanned": scanned},
            evidence=f"{len(results)} match(es) for '{args.query}' under {root}",
            verified=True,
        )

    async def read_file(args: ReadArgs, ctx: ToolContext):
        path = ctx.services.path_policy.validate(args.path, must_exist=True)
        if not path.is_file():
            return fail(f"'{path}' is not a file.")
        size = path.stat().st_size
        if size > MAX_READ_BYTES:
            return fail(
                f"File is {size} bytes; the text-read limit is {MAX_READ_BYTES} bytes.",
                hint="Use documents.extract_text for large PDFs/DOCX/XLSX or read a smaller file.",
            )
        raw = path.read_bytes()
        if b"\x00" in raw[:4096]:
            return fail("This appears to be a binary file.", hint="Use documents.extract_text or a specific document tool.")
        text = raw.decode("utf-8", errors="replace")
        return ok(
            {"path": str(path), "content": text[: args.max_chars], "chars": len(text), "truncated": len(text) > args.max_chars},
            evidence=f"Read {len(text)} characters from {path.name}",
        )

    async def create_file(args: CreateFileArgs, ctx: ToolContext):
        path = ctx.services.path_policy.validate(args.path, check_sensitive=False)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and not args.overwrite:
            return fail(f"'{path}' already exists.", hint="Set overwrite=true only if replacing it is intended.")
        if path.exists():
            backup = _backup_file(path, ctx)
        else:
            backup = None
        path.write_text(args.content, encoding="utf-8")
        return ok(
            {"path": str(path), "bytes": path.stat().st_size, "backup": backup},
            evidence=f"Wrote {path.stat().st_size} bytes to {path}",
            artifacts=[{"type": "file", "path": str(path)}],
        )

    async def create_folder(args: CreateFolderArgs, ctx: ToolContext):
        path = ctx.services.path_policy.validate(args.path, check_sensitive=False)
        existed = path.exists()
        path.mkdir(parents=True, exist_ok=True)
        if existed and not path.is_dir():
            return fail(f"'{path}' exists and is not a directory.")
        return ok({"path": str(path), "created": not existed}, evidence=f"Folder available at {path}")

    async def rename(args: PathPairArgs, ctx: ToolContext):
        source = ctx.services.path_policy.validate(args.source, must_exist=True)
        destination = ctx.services.path_policy.validate(args.destination, check_sensitive=False)
        if destination.exists() and not args.overwrite:
            return fail(f"Destination '{destination}' already exists.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        source.rename(destination)
        return ok({"source": str(source), "destination": str(destination)}, evidence=f"Renamed to {destination.name}")

    async def copy(args: PathPairArgs, ctx: ToolContext):
        source = ctx.services.path_policy.validate(args.source, must_exist=True)
        destination = ctx.services.path_policy.validate(args.destination, check_sensitive=False)
        if destination.exists() and not args.overwrite:
            return fail(f"Destination '{destination}' already exists.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, destination, dirs_exist_ok=args.overwrite)
        else:
            shutil.copy2(source, destination)
        return ok(
            {"source": str(source), "destination": str(destination)},
            evidence=f"Copied to {destination}",
            artifacts=[{"type": "file", "path": str(destination)}],
        )

    async def move(args: PathPairArgs, ctx: ToolContext):
        source = ctx.services.path_policy.validate(args.source, must_exist=True)
        destination = ctx.services.path_policy.validate(args.destination, check_sensitive=False)
        if destination.exists() and not args.overwrite:
            return fail(f"Destination '{destination}' already exists.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        return ok({"source": str(source), "destination": str(destination)}, evidence=f"Moved to {destination}")

    async def delete(args: DeleteArgs, ctx: ToolContext):
        path = ctx.services.path_policy.validate(args.path, must_exist=True)
        for root in ctx.services.path_policy.allowed:
            if path == root:
                return fail("Refusing to delete an allowed root directory.", hint="Delete a specific file or subfolder instead.")
        if path.is_dir() and any(path.iterdir()) and not args.recursive:
            return fail("Directory is not empty.", hint="Pass recursive=true to move the whole tree to the recycle backup.")
        target = _trash_path(path, ctx)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(target))
        manifest = {
            "deleted_at": datetime.now(timezone.utc).isoformat(),
            "original": str(path),
            "backup": str(target),
            "recursive": args.recursive,
        }
        manifest_path = target.parent / "manifest.json"
        existing = []
        if manifest_path.exists():
            try:
                existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                existing = []
        existing.append(manifest)
        manifest_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        return ok(
            {"deleted": str(path), "recoverable_at": str(target), "permanent": False},
            evidence=f"Moved {path.name} to recoverable trash ({target.parent.name})",
            artifacts=[{"type": "trash", "path": str(target)}],
        )

    async def metadata(args: PathArgs, ctx: ToolContext):
        path = ctx.services.path_policy.validate(args.path, must_exist=True)
        stat = path.stat()
        return ok(
            {
                "path": str(path),
                "name": path.name,
                "is_dir": path.is_dir(),
                "is_file": path.is_file(),
                "size": stat.st_size,
                "created": datetime.fromtimestamp(stat.st_ctime, timezone.utc).isoformat(),
                "modified": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                "extension": path.suffix.lower(),
            },
            evidence=f"Metadata for {path.name}",
        )

    async def file_size(args: PathArgs, ctx: ToolContext):
        path = ctx.services.path_policy.validate(args.path, must_exist=True)
        size = path.stat().st_size if path.is_file() else _tree_size(path)
        return ok({"path": str(path), "bytes": size, "mb": round(size / (1024 * 1024), 2)}, evidence=f"{size} bytes")

    async def find_duplicates(args: FindDuplicatesArgs, ctx: ToolContext):
        root = ctx.services.path_policy.validate(args.directory, must_exist=True)
        by_size: dict[int, list[Path]] = {}
        scanned = 0
        for file in root.rglob("*"):
            if not file.is_file():
                continue
            scanned += 1
            if scanned > args.max_files:
                break
            try:
                size = file.stat().st_size
            except OSError:
                continue
            if size == 0:
                continue
            by_size.setdefault(size, []).append(file)

        duplicates: list[dict[str, Any]] = []
        for size, files in by_size.items():
            if len(files) < 2:
                continue
            hashes: dict[str, list[str]] = {}
            for file in files:
                try:
                    digest = _hash_file(file)
                except OSError:
                    continue
                hashes.setdefault(digest, []).append(str(file))
            for digest, paths in hashes.items():
                if len(paths) > 1:
                    duplicates.append({"hash": digest, "size": size, "files": paths})
        return ok(
            {"duplicates": duplicates, "groups": len(duplicates), "scanned": scanned},
            evidence=f"{len(duplicates)} duplicate group(s) across {scanned} file(s)",
        )

    async def organize(args: OrganizeArgs, ctx: ToolContext):
        root = ctx.services.path_policy.validate(args.directory, must_exist=True)
        if not root.is_dir():
            return fail(f"'{root}' is not a directory.")
        moves = _build_plan(root, args)
        if not moves:
            return ok({"directory": str(root), "moves": [], "count": 0, "dry_run": args.dry_run}, evidence="Nothing to organize")
        if args.dry_run:
            return ok(
                {"directory": str(root), "moves": moves, "count": len(moves), "dry_run": True},
                evidence=f"Preview: {len(moves)} file(s) would be moved. Re-run with dry_run=false to apply.",
                verified=True,
            )
        applied: list[dict[str, str]] = []
        for move_item in moves:
            source = Path(move_item["source"])
            target = Path(move_item["destination"])
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                unique = _unique_path(target)
                shutil.move(str(source), str(unique))
                applied.append({"source": str(source), "destination": str(unique)})
            except OSError as exc:
                applied.append({"source": str(source), "destination": "", "error": str(exc)})
        manifest = ctx.services.settings.app.data_dir / "trash" / f"organize_{time.strftime('%Y%m%d_%H%M%S')}.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps(applied, indent=2), encoding="utf-8")
        failures = [item for item in applied if item.get("error")]
        result = ok(
            {"directory": str(root), "moved": applied, "count": len(applied), "failures": len(failures), "manifest": str(manifest), "dry_run": False},
            evidence=f"Moved {len(applied) - len(failures)} file(s); manifest at {manifest}",
            verified=not failures,
        )
        if failures:
            result.hint = "Some files could not be moved. Inspect the manifest and retry those entries."
        return result

    registry.register(build_tool(
        name="files.allowed_roots", description="List the directories JARVIS is permitted to access.",
        input_model=EmptyArgs, handler=allowed_roots, permission=ToolPermission.READ, tags=("files",),
    ))
    registry.register(build_tool(
        name="files.list_directory", description="List a directory inside the sandbox.",
        input_model=ListArgs, handler=list_directory, permission=ToolPermission.READ, tags=("files",),
    ))
    registry.register(build_tool(
        name="files.search", description="Recursively search filenames (and optionally text content) under a directory.",
        input_model=SearchArgs, handler=search, permission=ToolPermission.READ, timeout=90, tags=("files",),
    ))
    registry.register(build_tool(
        name="files.read_file", description="Read a UTF-8 text file (2 MB limit).",
        input_model=ReadArgs, handler=read_file, permission=ToolPermission.READ, tags=("files",),
    ))
    registry.register(build_tool(
        name="files.create_file", description="Create or overwrite a text file; existing files are backed up.",
        input_model=CreateFileArgs, handler=create_file, permission=ToolPermission.WRITE, tags=("files",),
    ))
    registry.register(build_tool(
        name="files.create_folder", description="Create a folder (parents included).",
        input_model=CreateFolderArgs, handler=create_folder, permission=ToolPermission.WRITE, tags=("files",),
    ))
    registry.register(build_tool(
        name="files.rename", description="Rename or relocate a file/folder.",
        input_model=PathPairArgs, handler=rename, permission=ToolPermission.WRITE, tags=("files",),
    ))
    registry.register(build_tool(
        name="files.copy", description="Copy a file or folder.",
        input_model=PathPairArgs, handler=copy, permission=ToolPermission.WRITE, timeout=120, tags=("files",),
    ))
    registry.register(build_tool(
        name="files.move", description="Move a file or folder.",
        input_model=PathPairArgs, handler=move, permission=ToolPermission.WRITE, timeout=120, tags=("files",),
    ))
    registry.register(build_tool(
        name="files.delete", description="Move a file/folder to recoverable trash (never permanent). Requires approval.",
        input_model=DeleteArgs, handler=delete, permission=ToolPermission.DESTRUCTIVE, timeout=60,
        requires_approval=True, approval_reason="Deleting files moves them to JARVIS trash and requires your approval.",
        tags=("files",),
    ))
    registry.register(build_tool(
        name="files.get_metadata", description="Return size and timestamps for a path.",
        input_model=PathArgs, handler=metadata, permission=ToolPermission.READ, tags=("files",),
    ))
    registry.register(build_tool(
        name="files.get_file_size", description="Return a file or directory size in bytes/MB.",
        input_model=PathArgs, handler=file_size, permission=ToolPermission.READ, tags=("files",),
    ))
    registry.register(build_tool(
        name="files.find_duplicates", description="Find duplicate files by size + SHA-256 hash.",
        input_model=FindDuplicatesArgs, handler=find_duplicates, permission=ToolPermission.READ, timeout=180, tags=("files",),
    ))
    registry.register(build_tool(
        name="files.preview_organization", description="Preview how files would be organized without moving anything.",
        input_model=OrganizeArgs, handler=organize, permission=ToolPermission.READ, timeout=30, tags=("files",),
    ))
    registry.register(build_tool(
        name="files.organize_directory", description="Organize a directory by category/extension/subject. Preview first; requires approval.",
        input_model=OrganizeArgs, handler=organize, permission=ToolPermission.DESTRUCTIVE, timeout=180,
        requires_approval=True, approval_reason="Bulk file moves require approval. Review the preview first.",
        tags=("files",),
    ))


# --- helpers ---------------------------------------------------------------
def _build_plan(root: Path, args: OrganizeArgs) -> list[dict[str, str]]:
    iterator = root.rglob("*") if args.include_subfolders else root.iterdir()
    moves: list[dict[str, str]] = []
    for entry in iterator:
        if not entry.is_file():
            continue
        extension = entry.suffix.lower()
        folder: str
        if args.mode == "by_extension":
            folder = extension.lstrip(".").upper() or "No extension"
        elif args.mode == "by_subject":
            folder = "Unsorted"
            lowered = entry.name.lower()
            for subject, keywords in args.subject_rules.items():
                if any(keyword.lower() in lowered for keyword in keywords):
                    folder = subject
                    break
        else:
            folder = "Other"
            for category, extensions in _CATEGORY_MAP.items():
                if extension in extensions:
                    folder = category
                    break
        target = root / folder / entry.name
        if entry.parent == target.parent:
            continue
        moves.append({"source": str(entry), "destination": str(target), "category": folder})
    return moves


def _backup_file(path: Path, ctx: ToolContext) -> str:
    backup_dir = ctx.services.settings.app.data_dir / "trash" / "overwrites" / time.strftime("%Y%m%d_%H%M%S")
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = _unique_path(backup_dir / path.name)
    shutil.copy2(path, target)
    return str(target)


def _trash_path(path: Path, ctx: ToolContext) -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    return _unique_path(ctx.services.settings.app.data_dir / "trash" / stamp / path.name)


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(1, 10000):
        candidate = path.with_name(f"{path.stem} ({index}){path.suffix}")
        if not candidate.exists():
            return candidate
    return path.with_name(f"{path.stem}_{uuid.uuid4().hex[:6]}{path.suffix}")


def _tree_size(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            try:
                total += item.stat().st_size
            except OSError:
                continue
    return total


def _hash_file(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(chunk):
            digest.update(block)
    return digest.hexdigest()
