"""Document tools: creation, extraction, validation and boundary handling."""

from __future__ import annotations

from pathlib import Path

import fitz


async def _run(services, ctx, name, arguments):
    result, _duration = await services.registry.execute(name, arguments, ctx)
    return result


def _make_pdf(path: Path, text: str = "JARVIS document test") -> None:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    document.save(str(path))
    document.close()


async def test_create_and_extract_docx(services, tool_context, sandbox):
    target = sandbox / "report.docx"
    created = await _run(
        services,
        tool_context,
        "documents.create_docx",
        {"content": "# Summary\n\nThis is a test document.\n\n- point one\n- point two", "output_path": str(target), "title": "Report"},
    )
    assert created.ok and target.exists() and target.stat().st_size > 0

    extracted = await _run(services, tool_context, "documents.extract_text", {"path": str(target)})
    assert extracted.ok
    assert "test document" in extracted.data["text"]


async def test_create_and_extract_xlsx(services, tool_context, sandbox):
    target = sandbox / "grades.xlsx"
    created = await _run(
        services,
        tool_context,
        "documents.create_xlsx",
        {
            "data": [{"Student": "Ada", "Score": 95}, {"Student": "Grace", "Score": 91}],
            "output_path": str(target),
            "sheet_name": "Grades",
        },
    )
    assert created.ok and target.exists()
    assert created.data["rows"] == 3

    extracted = await _run(services, tool_context, "documents.extract_text", {"path": str(target)})
    assert extracted.ok
    assert "Ada" in extracted.data["text"] and "Grace" in extracted.data["text"]


async def test_create_and_read_pdf(services, tool_context, sandbox):
    target = sandbox / "brief.pdf"
    created = await _run(
        services,
        tool_context,
        "documents.create_pdf",
        {"content": "# Brief\n\nA short generated brief about sandboxing.", "output_path": str(target), "title": "Brief"},
    )
    assert created.ok and target.exists()
    assert created.data["method"] in ("chromium", "pymupdf")

    read = await _run(services, tool_context, "documents.read_pdf", {"path": str(target)})
    assert read.ok
    assert read.data["page_count"] >= 1
    assert "sandboxing" in read.data["text"].lower()


async def test_pdf_extraction_of_existing_file(services, tool_context, sandbox):
    target = sandbox / "existing.pdf"
    _make_pdf(target, "Extracted PDF text")
    read = await _run(services, tool_context, "documents.read_pdf", {"path": str(target)})
    assert read.ok and "Extracted PDF text" in read.data["text"]


async def test_invalid_document_is_reported(services, tool_context, sandbox):
    broken = sandbox / "broken.pdf"
    broken.write_bytes(b"this is definitely not a pdf")
    result = await _run(services, tool_context, "documents.read_pdf", {"path": str(broken)})
    assert result.ok is False
    assert result.error

    wrong_type = sandbox / "plain.txt"
    wrong_type.write_text("text", encoding="utf-8")
    not_pdf = await _run(services, tool_context, "documents.read_pdf", {"path": str(wrong_type)})
    assert not_pdf.ok is False
    assert "not a PDF" in not_pdf.error


async def test_missing_input_is_reported(services, tool_context, sandbox):
    result = await _run(services, tool_context, "documents.extract_text", {"path": str(sandbox / "nope.docx")})
    assert result.ok is False
    assert "does not exist" in (result.error or "")


async def test_output_paths_are_validated(services, tool_context, tmp_path):
    outside = tmp_path / "escape.docx"
    result = await _run(
        services,
        tool_context,
        "documents.create_docx",
        {"content": "hello", "output_path": str(outside)},
    )
    assert result.ok is False
    assert "outside the allowed directories" in (result.error or "")
    assert not outside.exists()


async def test_large_text_extraction_is_bounded(services, tool_context, sandbox):
    target = sandbox / "large.txt"
    target.write_text("A" * 200_000, encoding="utf-8")
    result = await _run(
        services, tool_context, "documents.extract_text", {"path": str(target), "max_chars": 1000}
    )
    assert result.ok
    assert result.data["truncated"] is True
    assert len(result.data["text"]) == 1000


async def test_document_conversion(services, tool_context, sandbox):
    source = sandbox / "convert.docx"
    created = await _run(
        services,
        tool_context,
        "documents.create_docx",
        {"content": "Convertible content", "output_path": str(source)},
    )
    assert created.ok
    destination = sandbox / "convert.txt"
    converted = await _run(
        services, tool_context, "documents.convert_document", {"source": str(source), "destination": str(destination)}
    )
    assert converted.ok and destination.exists()
    assert "Convertible content" in destination.read_text(encoding="utf-8")
