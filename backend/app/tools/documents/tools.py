"""Document tools: PDF/DOCX/XLSX read, create, convert and summarize."""

from __future__ import annotations

import asyncio
import csv
import html
import re
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.core.enums import ToolPermission
from app.core.errors import ToolExecutionError
from app.tools.base import ToolContext, ToolRegistry, build_tool, fail, ok

MAX_TEXT_CHARS = 120_000


# --- schemas ---------------------------------------------------------------
class PathArgs(BaseModel):
    path: str


class ReadPdfArgs(BaseModel):
    path: str
    max_chars: int = Field(default=40000, ge=500, le=MAX_TEXT_CHARS)


class ExtractArgs(BaseModel):
    path: str
    max_chars: int = Field(default=40000, ge=500, le=MAX_TEXT_CHARS)


class CreatePdfArgs(BaseModel):
    content: str = Field(description="Markdown-like content (headings, lists, tables).")
    output_path: str
    title: str | None = None


class CreateDocxArgs(BaseModel):
    content: str = Field(description="Markdown-like content (headings, lists, tables).")
    output_path: str
    title: str | None = None


class CreateXlsxArgs(BaseModel):
    data: list[dict[str, Any]] | list[list[Any]] = Field(description="List of objects (headers inferred) or list of rows.")
    output_path: str
    sheet_name: str = "Sheet1"


class ConvertArgs(BaseModel):
    source: str
    destination: str


class SummarizeArgs(BaseModel):
    path: str
    focus: str = Field(default="", description="Optional aspect the user cares about.")


def register_document_tools(registry: ToolRegistry) -> None:
    async def read_pdf(args: ReadPdfArgs, ctx: ToolContext):
        path = ctx.services.path_policy.validate(args.path, must_exist=True)
        if path.suffix.lower() != ".pdf":
            return fail(f"'{path.suffix}' is not a PDF.", hint="Use documents.extract_text for other formats.")
        try:
            import fitz
        except ImportError as exc:
            raise ToolExecutionError("PyMuPDF is required to read PDFs.") from exc
        document = fitz.open(str(path))
        try:
            pages = []
            total = 0
            for index, page in enumerate(document):
                text = page.get_text("text")
                total += len(text)
                pages.append({"page": index + 1, "text": text})
                if total >= args.max_chars:
                    break
            text = "\n\n".join(f"[Page {item['page']}]\n{item['text']}" for item in pages)
            return ok(
                {
                    "path": str(path),
                    "page_count": document.page_count,
                    "metadata": {key: str(value) for key, value in (document.metadata or {}).items() if value},
                    "text": text[: args.max_chars],
                    "truncated": total > args.max_chars,
                    "chars": len(text),
                },
                evidence=f"Read {document.page_count}-page PDF, extracted {len(text)} characters",
            )
        finally:
            document.close()

    async def extract_text(args: ExtractArgs, ctx: ToolContext):
        path = ctx.services.path_policy.validate(args.path, must_exist=True)
        text, meta = _extract(path)
        return ok(
            {"path": str(path), "text": text[: args.max_chars], "chars": len(text), "truncated": len(text) > args.max_chars, **meta},
            evidence=f"Extracted {len(text)} characters from {path.name}",
        )

    async def create_pdf(args: CreatePdfArgs, ctx: ToolContext):
        path = ctx.services.path_policy.validate(args.output_path, check_sensitive=False)
        path.parent.mkdir(parents=True, exist_ok=True)
        title = args.title or path.stem.replace("_", " ").title()
        html_doc = _markdown_to_html(args.content, title)
        method = "chromium"
        try:
            await _render_pdf_chromium(html_doc, path, title)
        except Exception:
            _render_pdf_fitz(args.content, path, title)
            method = "pymupdf"
        if not path.exists() or path.stat().st_size == 0:
            return fail("PDF generation produced no file.")
        return ok(
            {"path": str(path), "bytes": path.stat().st_size, "method": method, "title": title},
            evidence=f"Created PDF {path.name} ({path.stat().st_size} bytes) via {method}",
            artifacts=[{"type": "file", "path": str(path)}],
        )

    async def create_docx(args: CreateDocxArgs, ctx: ToolContext):
        path = ctx.services.path_policy.validate(args.output_path, check_sensitive=False)
        path.parent.mkdir(parents=True, exist_ok=True)
        _render_docx(args.content, path, args.title or path.stem.replace("_", " ").title())
        if not path.exists():
            return fail("DOCX generation produced no file.")
        return ok(
            {"path": str(path), "bytes": path.stat().st_size},
            evidence=f"Created DOCX {path.name} ({path.stat().st_size} bytes)",
            artifacts=[{"type": "file", "path": str(path)}],
        )

    async def create_xlsx(args: CreateXlsxArgs, ctx: ToolContext):
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font, PatternFill
        except ImportError as exc:
            raise ToolExecutionError("openpyxl is required to create spreadsheets.") from exc
        path = ctx.services.path_policy.validate(args.output_path, check_sensitive=False)
        path.parent.mkdir(parents=True, exist_ok=True)
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = re.sub(r"[\\/*?:\[\]]", "", args.sheet_name)[:31] or "Sheet1"

        rows = args.data
        if rows and isinstance(rows[0], dict):
            headers: list[str] = []
            for row in rows:
                for key in row:
                    if key not in headers:
                        headers.append(str(key))
            sheet.append(headers)
            for row in rows:
                sheet.append([_cell_value(row.get(header, "")) for header in headers])
        else:
            for row in rows:
                if isinstance(row, list):
                    sheet.append([_cell_value(value) for value in row])

        for cell in sheet[1]:
            cell.font = Font(bold=True)
            cell.fill = PatternFill("solid", fgColor="5C1020")
            cell.font = Font(bold=True, color="FFFFFF")
        for column in sheet.columns:
            letter = column[0].column_letter
            width = max((len(str(cell.value or "")) for cell in column), default=8)
            sheet.column_dimensions[letter].width = min(max(12, width + 2), 60)
        workbook.save(path)
        return ok(
            {"path": str(path), "rows": sheet.max_row, "columns": sheet.max_column},
            evidence=f"Created XLSX with {sheet.max_row} rows",
            artifacts=[{"type": "file", "path": str(path)}],
        )

    async def convert(args: ConvertArgs, ctx: ToolContext):
        source = ctx.services.path_policy.validate(args.source, must_exist=True)
        destination = ctx.services.path_policy.validate(args.destination, check_sensitive=False)
        destination.parent.mkdir(parents=True, exist_ok=True)
        source_ext = source.suffix.lower()
        target_ext = destination.suffix.lower()
        text, _meta = _extract(source)
        if target_ext == ".txt" or target_ext == ".md":
            destination.write_text(text, encoding="utf-8")
            method = "text"
        elif target_ext == ".docx":
            _render_docx(text, destination, source.stem)
            method = "docx"
        elif target_ext == ".pdf":
            try:
                await _render_pdf_chromium(_markdown_to_html(text, source.stem), destination, source.stem)
                method = "chromium"
            except Exception:
                _render_pdf_fitz(text, destination, source.stem)
                method = "pymupdf"
        elif target_ext == ".csv" and source_ext in (".xlsx", ".xlsm"):
            from openpyxl import load_workbook

            workbook = load_workbook(source, read_only=True, data_only=True)
            sheet = workbook.active
            with destination.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                for row in sheet.iter_rows(values_only=True):
                    writer.writerow(["" if value is None else value for value in row])
            workbook.close()
            method = "openpyxl"
        else:
            return fail(
                f"Unsupported conversion {source_ext} → {target_ext}.",
                hint="Supported targets: .pdf, .docx, .txt, .md; .xlsx → .csv.",
            )
        return ok(
            {"source": str(source), "destination": str(destination), "method": method, "bytes": destination.stat().st_size},
            evidence=f"Converted {source.name} to {destination.name}",
            artifacts=[{"type": "file", "path": str(destination)}],
        )

    async def summarize(args: SummarizeArgs, ctx: ToolContext):
        path = ctx.services.path_policy.validate(args.path, must_exist=True)
        text, _meta = _extract(path)
        if not text.strip():
            return fail("No extractable text was found in the document.")
        provider = await ctx.services.provider_manager.get()
        from app.providers.base import ChatMessage

        chunks = [text[i : i + 9000] for i in range(0, min(len(text), 54000), 9000)]
        partials: list[str] = []
        for index, chunk in enumerate(chunks):
            response = await provider.chat(
                [
                    ChatMessage(role="system", content="Summarize the document section factually and concisely. Do not invent details."),
                    ChatMessage(role="user", content=f"Section {index + 1}/{len(chunks)}:\n\n{chunk}"),
                ],
                temperature=0.1,
            )
            partials.append(response.content.strip())
        combined = "\n\n".join(partials)
        focus = f" Focus especially on: {args.focus}." if args.focus else ""
        final = await provider.chat(
            [
                ChatMessage(role="system", content="Produce a structured summary with headings and bullet points. Only use the supplied text."),
                ChatMessage(role="user", content=f"Document: {path.name}.{focus}\n\nSection summaries:\n{combined}"),
            ],
            temperature=0.2,
        )
        return ok(
            {"path": str(path), "summary": final.content, "chars_read": len(text)},
            evidence=f"Summarized {path.name} ({len(text)} characters) via {provider.name}",
            verified=bool(final.content.strip()),
        )

    registry.register(build_tool(
        name="documents.read_pdf", description="Read a PDF: page count, metadata and extracted text.",
        input_model=ReadPdfArgs, handler=read_pdf, permission=ToolPermission.READ, timeout=90, tags=("documents",),
    ))
    registry.register(build_tool(
        name="documents.extract_text", description="Extract text from PDF, DOCX, XLSX, CSV, MD or TXT.",
        input_model=ExtractArgs, handler=extract_text, permission=ToolPermission.READ, timeout=90, tags=("documents",),
    ))
    registry.register(build_tool(
        name="documents.create_pdf", description="Generate a formatted PDF from markdown-like content.",
        input_model=CreatePdfArgs, handler=create_pdf, permission=ToolPermission.WRITE, timeout=120, tags=("documents",),
    ))
    registry.register(build_tool(
        name="documents.create_docx", description="Generate a formatted Word document from markdown-like content.",
        input_model=CreateDocxArgs, handler=create_docx, permission=ToolPermission.WRITE, timeout=60, tags=("documents",),
    ))
    registry.register(build_tool(
        name="documents.create_xlsx", description="Generate an Excel workbook from rows or objects.",
        input_model=CreateXlsxArgs, handler=create_xlsx, permission=ToolPermission.WRITE, timeout=60, tags=("documents",),
    ))
    registry.register(build_tool(
        name="documents.convert_document", description="Convert between PDF, DOCX, TXT/MD, and XLSX→CSV.",
        input_model=ConvertArgs, handler=convert, permission=ToolPermission.WRITE, timeout=120, tags=("documents",),
    ))
    registry.register(build_tool(
        name="documents.summarize_document", description="Summarize a document using the configured LLM.",
        input_model=SummarizeArgs, handler=summarize, permission=ToolPermission.READ, timeout=180, tags=("documents",),
    ))


# --- extraction ------------------------------------------------------------
def _extract(path: Path) -> tuple[str, dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        import fitz

        document = fitz.open(str(path))
        try:
            text = "\n\n".join(page.get_text("text") for page in document)
            return text[:MAX_TEXT_CHARS], {"type": "pdf", "pages": document.page_count}
        finally:
            document.close()
    if suffix == ".docx":
        from docx import Document

        document = Document(str(path))
        parts: list[str] = []
        for paragraph in document.paragraphs:
            if paragraph.text.strip():
                style = paragraph.style.name if paragraph.style else ""
                prefix = "## " if style.lower().startswith("heading") else ""
                parts.append(f"{prefix}{paragraph.text}")
        for table in document.tables:
            for row in table.rows:
                parts.append(" | ".join(cell.text.strip() for cell in row.cells))
        return "\n".join(parts)[:MAX_TEXT_CHARS], {"type": "docx"}
    if suffix in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook

        workbook = load_workbook(path, read_only=True, data_only=True)
        try:
            parts = []
            for sheet in workbook.worksheets:
                parts.append(f"## Sheet: {sheet.title}")
                for row in sheet.iter_rows(values_only=True):
                    if any(value is not None for value in row):
                        parts.append(" | ".join("" if value is None else str(value) for value in row))
            return "\n".join(parts)[:MAX_TEXT_CHARS], {"type": "xlsx", "sheets": len(workbook.worksheets)}
        finally:
            workbook.close()
    if suffix in (".txt", ".md", ".csv", ".log", ".json", ".html", ".htm", ".xml", ".yaml", ".yml"):
        return path.read_text(encoding="utf-8", errors="replace")[:MAX_TEXT_CHARS], {"type": suffix.lstrip(".")}
    raise ToolExecutionError(
        f"Unsupported document type '{suffix}'.",
        hint="Supported: PDF, DOCX, XLSX, CSV, TXT, MD, JSON, HTML, XML, YAML.",
    )


# --- rendering -------------------------------------------------------------
def _markdown_to_html(markdown: str, title: str) -> str:
    lines = markdown.replace("\r\n", "\n").split("\n")
    body: list[str] = []
    in_list = False
    table_rows: list[list[str]] = []

    def flush_table() -> None:
        nonlocal table_rows
        if not table_rows:
            return
        rows = [row for row in table_rows if not all(re.fullmatch(r"[-: ]+", cell) for cell in row)]
        body.append("<table>")
        for index, row in enumerate(rows):
            tag = "th" if index == 0 else "td"
            body.append("<tr>" + "".join(f"<{tag}>{_inline(cell.strip())}</{tag}>" for cell in row) + "</tr>")
        body.append("</table>")
        table_rows = []

    def flush_list() -> None:
        nonlocal in_list
        if in_list:
            body.append("</ul>")
            in_list = False

    for raw in lines:
        line = raw.rstrip()
        if line.strip().startswith("|") and line.strip().endswith("|"):
            if not table_rows:
                flush_list()
            table_rows.append([cell for cell in line.strip().strip("|").split("|")])
            continue
        flush_table()
        if not line.strip():
            flush_list()
            body.append("<p></p>")
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            flush_list()
            level = len(heading.group(1))
            body.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            continue
        bullet = re.match(r"^\s*[-*]\s+(.*)$", line)
        if bullet:
            if not in_list:
                body.append("<ul>")
                in_list = True
            body.append(f"<li>{_inline(bullet.group(1))}</li>")
            continue
        numbered = re.match(r"^\s*\d+[.)]\s+(.*)$", line)
        if numbered:
            flush_list()
            body.append(f"<p class='numbered'>{_inline(numbered.group(1))}</p>")
            continue
        if line.startswith("---"):
            flush_list()
            body.append("<hr/>")
            continue
        flush_list()
        body.append(f"<p>{_inline(line)}</p>")
    flush_list()
    flush_table()
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>
@page {{ size: A4; margin: 22mm 18mm; }}
body {{ font-family: 'Segoe UI', Calibri, Arial, sans-serif; font-size: 11.5pt; line-height: 1.55; color: #1c1416; }}
h1 {{ font-size: 22pt; color: #5c1020; border-bottom: 2px solid #8a1730; padding-bottom: 6px; margin-top: 26px; }}
h2 {{ font-size: 16pt; color: #6d1426; margin-top: 22px; }}
h3 {{ font-size: 13pt; color: #3a2228; margin-top: 18px; }}
h4, h5, h6 {{ font-size: 12pt; color: #3a2228; }}
p {{ margin: 7px 0; }}
ul {{ margin: 6px 0 10px 22px; }}
li {{ margin: 3px 0; }}
table {{ width: 100%; border-collapse: collapse; margin: 12px 0; font-size: 10pt; }}
th {{ background: #5c1020; color: #fff; text-align: left; padding: 7px 8px; }}
td {{ border: 1px solid #d8c8cb; padding: 6px 8px; }}
tr:nth-child(even) td {{ background: #faf4f5; }}
hr {{ border: none; border-top: 1px solid #d8c8cb; margin: 18px 0; }}
code {{ background: #f3eaec; padding: 1px 4px; border-radius: 3px; font-family: Consolas, monospace; }}
.header {{ font-size: 9pt; color: #8a7379; text-align: right; margin-bottom: 20px; letter-spacing: 1px; }}
</style></head>
<body><div class="header">JARVIS · {html.escape(time.strftime('%Y-%m-%d'))}</div>
{''.join(body)}
</body></html>"""


def _inline(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", escaped)
    escaped = re.sub(r"`(.+?)`", r"<code>\1</code>", escaped)
    return escaped


async def _render_pdf_chromium(html_doc: str, path: Path, title: str) -> None:
    from playwright.async_api import async_playwright

    playwright = await async_playwright().start()
    try:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(html_doc, wait_until="load")
        await page.pdf(path=str(path), format="A4", print_background=True, display_header_footer=False)
        await browser.close()
    finally:
        await playwright.stop()


def _render_pdf_fitz(text: str, path: Path, title: str) -> None:
    import fitz

    document = fitz.open()
    margin = 56
    page_width, page_height = 595, 842
    content = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    lines = content.replace("\r\n", "\n").split("\n")
    while lines:
        page = document.new_page(width=page_width, height=page_height)
        page.insert_text((margin, 50), title[:80], fontsize=14, color=(0.36, 0.06, 0.12))
        rect = fitz.Rect(margin, 70, page_width - margin, page_height - margin)
        chunk_lines: list[str] = []
        used = 0
        while lines and used < 48:
            chunk_lines.append(lines.pop(0))
            used += 1
        page.insert_textbox(rect, "\n".join(chunk_lines), fontsize=10.5, lineheight=1.35)
    if document.page_count == 0:
        document.new_page()
    document.save(str(path))
    document.close()


def _render_docx(text: str, path: Path, title: str) -> None:
    from docx import Document
    from docx.shared import Pt, RGBColor

    document = Document()
    styles = document.styles["Normal"]
    styles.font.name = "Calibri"
    styles.font.size = Pt(11)

    document.add_heading(title, level=0)
    lines = text.replace("\r\n", "\n").split("\n")
    table_buffer: list[list[str]] = []

    def flush_table() -> None:
        nonlocal table_buffer
        if table_buffer:
            rows = [row for row in table_buffer if not all(re.fullmatch(r"[-: ]+", cell) for cell in row)]
            if rows:
                table = document.add_table(rows=len(rows), cols=max(len(row) for row in rows))
                table.style = "Light Grid Accent 1"
                for row_index, row in enumerate(rows):
                    for col_index, cell in enumerate(row):
                        table.cell(row_index, col_index).text = cell.strip()
            table_buffer = []

    for raw in lines:
        line = raw.rstrip()
        if line.strip().startswith("|") and line.strip().endswith("|"):
            table_buffer.append(line.strip().strip("|").split("|"))
            continue
        flush_table()
        if not line.strip():
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            document.add_heading(heading.group(2), level=min(len(heading.group(1)), 4))
            continue
        bullet = re.match(r"^\s*[-*]\s+(.*)$", line)
        if bullet:
            document.add_paragraph(bullet.group(1), style="List Bullet")
            continue
        numbered = re.match(r"^\s*\d+[.)]\s+(.*)$", line)
        if numbered:
            document.add_paragraph(numbered.group(1), style="List Number")
            continue
        document.add_paragraph(line)
    flush_table()
    document.save(str(path))


def _cell_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value)
    return str(value)
