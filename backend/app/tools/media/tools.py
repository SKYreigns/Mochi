"""Media research tools.

Design constraints:
- metadata via yt-dlp (optional dependency),
- downloads only when the source explicitly declares a permissive license
  (Creative Commons / public domain) or the extractor reports no DRM and the
  media is a direct file,
- no DRM circumvention, no access-control bypass, no cookie extraction.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from app.core.enums import ToolPermission
from app.core.errors import ToolExecutionError
from app.tools.base import ToolContext, ToolRegistry, build_tool, fail, ok
from app.tools.browser.manager import _safe_filename, _unique_path

_PERMISSIVE_LICENSE_HINTS = (
    "creative commons",
    "cc-by",
    "cc by",
    "cc0",
    "public domain",
    "public-domain",
    "cc-by-sa",
)


class SearchArgs(BaseModel):
    query: str
    limit: int = Field(default=8, ge=1, le=20)


class MetadataArgs(BaseModel):
    url: str


class DownloadArgs(BaseModel):
    url: str
    directory: str | None = Field(default=None, description="Destination folder (must be inside allowed roots).")


def _ytdlp():
    try:
        import yt_dlp  # type: ignore
    except ImportError as exc:
        raise ToolExecutionError(
            "yt-dlp is not installed, so media search/metadata is unavailable.",
            hint="pip install -r backend/requirements-optional.txt",
        ) from exc
    return yt_dlp


def register_media_tools(registry: ToolRegistry) -> None:
    async def search(args: SearchArgs, ctx: ToolContext):
        yt_dlp = _ytdlp()
        options = {"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True}
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(f"ytsearch{args.limit}:{args.query}", download=False)
        except Exception as exc:
            return fail(f"Media search failed: {exc}", hint="Check the network connection or try a more specific query.", retryable=True)
        results = []
        for entry in (info or {}).get("entries") or []:
            if not entry:
                continue
            results.append(
                {
                    "title": entry.get("title"),
                    "url": entry.get("url") or entry.get("webpage_url"),
                    "duration_seconds": entry.get("duration"),
                    "uploader": entry.get("uploader") or entry.get("channel"),
                }
            )
        return ok({"results": results, "count": len(results)}, evidence=f"{len(results)} media results for '{args.query}'")

    async def metadata(args: MetadataArgs, ctx: ToolContext):
        yt_dlp = _ytdlp()
        try:
            with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
                info = ydl.extract_info(args.url, download=False)
        except Exception as exc:
            return fail(f"Could not read media metadata: {exc}", retryable=True)
        license_value = str(info.get("license") or "")
        permitted = any(hint in license_value.lower() for hint in _PERMISSIVE_LICENSE_HINTS)
        return ok(
            {
                "title": info.get("title"),
                "uploader": info.get("uploader"),
                "duration_seconds": info.get("duration"),
                "license": license_value or "standard/unspecified",
                "downloadable_by_policy": permitted,
                "is_live": info.get("is_live"),
                "webpage_url": info.get("webpage_url") or args.url,
                "description": (info.get("description") or "")[:2000],
            },
            evidence=f"Metadata retrieved; license: {license_value or 'unspecified'}",
        )

    async def download_authorized(args: DownloadArgs, ctx: ToolContext):
        yt_dlp = _ytdlp()
        try:
            with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
                info = ydl.extract_info(args.url, download=False)
        except Exception as exc:
            return fail(f"Could not evaluate the media license: {exc}", retryable=True)

        license_value = str(info.get("license") or "").lower()
        if not any(hint in license_value for hint in _PERMISSIVE_LICENSE_HINTS):
            return fail(
                "This media does not declare a permissive license, so JARVIS will not download it.",
                hint="Use an official source that permits downloading, or media licensed under Creative Commons / public domain.",
            )
        if info.get("_has_drm"):
            return fail("This media is DRM-protected. JARVIS does not circumvent DRM.")

        if args.directory:
            target_dir = ctx.services.path_policy.validate(args.directory, check_sensitive=False)
        else:
            target_dir = ctx.services.settings.app.data_dir / "downloads" / "media"
        target_dir.mkdir(parents=True, exist_ok=True)
        template = str(target_dir / "%(title).120s [%(id)s].%(ext)s")
        options = {
            "quiet": True,
            "no_warnings": True,
            "outtmpl": template,
            "noplaylist": True,
            "restrictfilenames": True,
        }
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                result = ydl.extract_info(args.url, download=True)
            path_value = result.get("requested_downloads", [{}])[0].get("filepath") if result else None
            if not path_value:
                candidates = sorted(target_dir.glob("*"), key=lambda item: item.stat().st_mtime, reverse=True)
                path_value = str(candidates[0]) if candidates else ""
            if not path_value or not Path(path_value).exists():
                return fail("The download completed without producing a verified file.")
            target = Path(path_value)
            return ok(
                {
                    "path": str(target),
                    "filename": target.name,
                    "bytes": target.stat().st_size,
                    "license": result.get("license") if result else license_value,
                    "title": result.get("title") if result else info.get("title"),
                },
                evidence=f"Downloaded {target.name} ({target.stat().st_size} bytes) under license: {license_value}",
                artifacts=[{"type": "file", "path": str(target)}],
            )
        except Exception as exc:
            return fail(f"Authorized download failed: {exc}", hint="Verify the URL and network connection.", retryable=True)

    registry.register(build_tool(
        name="media.search", description="Search for media metadata via yt-dlp (no download).",
        input_model=SearchArgs, handler=search, permission=ToolPermission.NAVIGATE, timeout=90, tags=("media",),
    ))
    registry.register(build_tool(
        name="media.get_metadata", description="Retrieve title, creator, duration and license for a media URL.",
        input_model=MetadataArgs, handler=metadata, permission=ToolPermission.READ, timeout=90, tags=("media",),
    ))
    registry.register(build_tool(
        name="media.download_authorized",
        description="Download media only when its declared license permits it (Creative Commons / public domain). Refuses DRM and standard-license content.",
        input_model=DownloadArgs, handler=download_authorized, permission=ToolPermission.WRITE, timeout=300,
        tags=("media",),
    ))
