"""LMS tools exposed to the agent and the assignment workspace API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.core.enums import ToolPermission
from app.tools.base import EmptyArgs, ToolContext, ToolRegistry, build_tool, ok


class OpenArgs(BaseModel):
    url: str | None = Field(default=None, description="LMS URL; defaults to the configured portal.")


class SnapshotArgs(BaseModel):
    url: str | None = None


class CourseListArgs(BaseModel):
    pass


class AssignmentListArgs(BaseModel):
    course_url: str | None = Field(default=None, description="Course page URL to inspect.")


class ReadAssignmentArgs(BaseModel):
    url: str


class DownloadArgs(BaseModel):
    url: str
    subject: str | None = None
    filename: str | None = None


class SaveAssignmentArgs(BaseModel):
    subject: str
    title: str
    instructions: str
    deadline: str | None = None
    lms_url: str = ""
    source_url: str = ""
    resources: list[dict[str, Any]] = Field(default_factory=list)


class SubmissionArgs(BaseModel):
    url: str


class AttachArgs(BaseModel):
    url: str | None = None
    selector: str = Field(description="CSS selector for the file input.")
    file_path: str


class SubmitArgs(BaseModel):
    url: str
    submit_selector: str | None = None
    assignment_id: str | None = None


def register_lms_tools(registry: ToolRegistry) -> None:
    async def open_lms(args: OpenArgs, ctx: ToolContext):
        data = await ctx.services.lms.open(args.url)
        return ok(data, evidence=f"Opened LMS page {data['url']}")

    async def check_login(args: SnapshotArgs, ctx: ToolContext):
        data = await ctx.services.lms.snapshot(args.url)
        evidence = "Logged in" if data["logged_in"] else "Login challenge detected — manual authentication required"
        result = ok(
            {
                "url": data.get("url"),
                "title": data.get("title"),
                "logged_in": data["logged_in"],
                "login_required": data["login_required"],
                "challenge_detected": data["challenge_detected"],
            },
            evidence=evidence,
            verified=True,
        )
        if data["login_required"]:
            result.hint = "Ask the user to sign in manually in the browser window. Never attempt to bypass MFA or CAPTCHA."
        return result

    async def list_courses(args: CourseListArgs, ctx: ToolContext):
        courses = await ctx.services.lms.list_courses()
        return ok({"courses": courses, "count": len(courses)}, evidence=f"{len(courses)} course link(s) found")

    async def list_assignments(args: AssignmentListArgs, ctx: ToolContext):
        assignments = await ctx.services.lms.list_assignments(args.course_url)
        return ok({"assignments": assignments, "count": len(assignments)}, evidence=f"{len(assignments)} assignment link(s) found")

    async def read_assignment(args: ReadAssignmentArgs, ctx: ToolContext):
        data = await ctx.services.lms.read_assignment(args.url)
        return ok(
            data,
            evidence=f"Read assignment '{data['title']}' ({len(data['attachments'])} attachment link(s), due: {data['due'] or 'unknown'})",
        )

    async def download_attachment(args: DownloadArgs, ctx: ToolContext):
        data = await ctx.services.lms.download_attachment(args.url, args.subject, args.filename)
        return ok(
            data,
            evidence=f"Downloaded {data['filename']} ({data['bytes']} bytes)",
            artifacts=[{"type": "file", "path": data["path"]}],
        )

    async def save_assignment(args: SaveAssignmentArgs, ctx: ToolContext):
        assignment = await ctx.services.lms.save_assignment(
            ctx.session,
            subject=args.subject,
            title=args.title,
            instructions=args.instructions,
            deadline=args.deadline,
            lms_url=args.lms_url,
            source_url=args.source_url,
            resources=args.resources,
        )
        return ok(
            {"assignment_id": assignment.id, "subject": assignment.subject, "title": assignment.title},
            evidence=f"Assignment workspace saved ({assignment.id})",
        )

    async def list_saved(args: BaseModel, ctx: ToolContext):
        assignments = await ctx.services.lms.list_saved(ctx.session)
        return ok(
            {
                "assignments": [
                    {
                        "id": item.id,
                        "subject": item.subject,
                        "title": item.title,
                        "deadline": item.deadline,
                        "status": item.status,
                        "submission_status": item.submission_status,
                        "source_url": item.source_url,
                    }
                    for item in assignments
                ]
            },
            evidence=f"{len(assignments)} saved assignment(s)",
        )

    async def submission_page(args: SubmissionArgs, ctx: ToolContext):
        data = await ctx.services.lms.submission_page(args.url)
        return ok(data, evidence=f"Submission page inspected: {len(data['file_inputs'])} file input(s)")

    async def attach_file(args: AttachArgs, ctx: ToolContext):
        path = ctx.services.path_policy.validate(args.file_path, must_exist=True)
        data = await ctx.services.lms.attach_submission_file(args.url, args.selector, str(path))
        return ok(data, evidence=f"Attached {data['attached']} to the submission form (not submitted)")

    async def submit_assignment(args: SubmitArgs, ctx: ToolContext):
        data = await ctx.services.lms.submit(ctx.session, args.url, args.submit_selector, args.assignment_id)
        evidence = (
            f"Submission receipt detected on {data['url']}"
            if data["receipt_found"]
            else "Submit action was performed, but no receipt was detected — verify manually"
        )
        from app.tools.base import ToolResult

        return ToolResult(
            ok=True,
            data=data,
            evidence=evidence,
            verified=data["receipt_found"],
            artifacts=[
                {"type": "screenshot", "path": data["screenshot_before"]["path"], "url": data["screenshot_before"]["url"]},
                {"type": "screenshot", "path": data["screenshot_after"]["path"], "url": data["screenshot_after"]["url"]},
            ],
        )

    registry.register(build_tool(
        name="lms.open", description="Open the configured LMS portal (or a given LMS URL) in the persistent browser.",
        input_model=OpenArgs, handler=open_lms, permission=ToolPermission.NAVIGATE, timeout=60, tags=("lms",),
    ))
    registry.register(build_tool(
        name="lms.check_login",
        description="Check whether the LMS session is authenticated and whether a CAPTCHA/MFA challenge is present.",
        input_model=SnapshotArgs, handler=check_login, permission=ToolPermission.READ, timeout=60, tags=("lms",),
    ))
    registry.register(build_tool(
        name="lms.list_courses", description="List course links on the LMS dashboard.",
        input_model=CourseListArgs, handler=list_courses, permission=ToolPermission.READ, timeout=60, tags=("lms",),
    ))
    registry.register(build_tool(
        name="lms.list_assignments", description="List assignment links for a course (or the LMS dashboard).",
        input_model=AssignmentListArgs, handler=list_assignments, permission=ToolPermission.READ, timeout=90, tags=("lms",),
    ))
    registry.register(build_tool(
        name="lms.read_assignment",
        description="Read an assignment page: title, deadline, description and attachment links.",
        input_model=ReadAssignmentArgs, handler=read_assignment, permission=ToolPermission.READ, timeout=90, tags=("lms",),
    ))
    registry.register(build_tool(
        name="lms.download_attachment",
        description="Download an authorized assignment attachment and verify it on disk.",
        input_model=DownloadArgs, handler=download_attachment, permission=ToolPermission.WRITE, timeout=120, tags=("lms",),
    ))
    registry.register(build_tool(
        name="lms.save_assignment",
        description="Store assignment details in the local assignment workspace.",
        input_model=SaveAssignmentArgs, handler=save_assignment, permission=ToolPermission.WRITE, timeout=30, tags=("lms",),
    ))
    registry.register(build_tool(
        name="lms.list_saved_assignments", description="List assignments saved in the workspace.",
        input_model=EmptyArgs, handler=list_saved, permission=ToolPermission.READ, timeout=30, tags=("lms",),
    ))
    registry.register(build_tool(
        name="lms.open_submission_page",
        description="Inspect a submission page: file inputs and submit buttons. Does not submit.",
        input_model=SubmissionArgs, handler=submission_page, permission=ToolPermission.NAVIGATE, timeout=60, tags=("lms",),
    ))
    registry.register(build_tool(
        name="lms.attach_submission_file",
        description="Attach a local file to the LMS submission form without submitting it.",
        input_model=AttachArgs, handler=attach_file, permission=ToolPermission.WRITE, timeout=60, tags=("lms",),
    ))
    registry.register(build_tool(
        name="lms.submit_assignment",
        description="Submit the prepared assignment. REQUIRES explicit approval; verifies a receipt and never claims success without one.",
        input_model=SubmitArgs, handler=submit_assignment, permission=ToolPermission.SENSITIVE, timeout=180,
        requires_approval=True,
        approval_reason="Submitting coursework is irreversible and requires explicit approval of the exact submission.",
        tags=("lms",),
    ))
