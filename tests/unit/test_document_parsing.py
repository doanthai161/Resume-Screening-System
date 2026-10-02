import asyncio
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from pypdf import PdfReader, PdfWriter

from app.core.config import settings
from app.workers.document import (
    ResumeDocument,
    _analyze_pdf,
    _analyze_docx,
    _join_text,
    analyze_document,
    pdf_ocr_mode,
)
from app.workers.errors import (
    PermanentParseError,
    TransientParseError,
    ProviderResponseError,
)
from app.workers.mineru_adapter import MinerUAdapter, _restore_docx_margins
from app.workers.quality import evaluate_text_quality
from scripts.generate_parse_samples import digital_pdf, docx_bytes


def document(content, extension=".pdf"):
    return ResumeDocument(SimpleNamespace(), content, extension, "cv" + extension)


def test_one_scanned_page_among_ten_still_requires_ocr():
    writer = PdfWriter()
    for _ in range(9):
        writer.add_page(PdfReader(BytesIO(digital_pdf())).pages[0])
    writer.add_blank_page(width=612, height=792)
    data = BytesIO()
    writer.write(data)
    analysis = _analyze_pdf(document(data.getvalue()))
    assert analysis.page_count == 10
    assert analysis.text_page_ratio == 0.9
    assert analysis.requires_ocr and pdf_ocr_mode(analysis) == "ocr"


def test_scan_cannot_silently_use_text_when_ocr_disabled(monkeypatch):
    from app.workers.document import DocumentAnalysis

    monkeypatch.setattr(settings, "MINERU_AUTO_OCR", False)
    with pytest.raises(PermanentParseError, match="requires OCR"):
        pdf_ocr_mode(DocumentAnalysis(2, "text", 0.5, True))


def test_native_text_is_not_silently_truncated():
    with pytest.raises(PermanentParseError) as caught:
        _join_text(["a" * 1_000_001])
    assert caught.value.code == "document_text_too_large"
    with pytest.raises(PermanentParseError):
        _join_text(
            ["\u1ec7" * 400_000]
        )  # JSON storage limit, despite fewer than 1M characters.


@pytest.mark.parametrize("kind", ["corrupt", "encrypted", "too_many_pages"])
def test_reject_unsafe_pdf_preflight(kind, monkeypatch):
    content = b"%PDF-1.7 broken"
    if kind != "corrupt":
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        if kind == "encrypted":
            writer.encrypt("secret")
        else:
            writer.add_blank_page(width=612, height=792)
            monkeypatch.setattr(settings, "MAX_RESUME_PAGES", 1)
        data = BytesIO()
        writer.write(data)
        content = data.getvalue()
    with pytest.raises(PermanentParseError) as caught:
        _analyze_pdf(document(content))
    assert (
        caught.value.code
        == {
            "corrupt": "invalid_pdf",
            "encrypted": "encrypted_pdf",
            "too_many_pages": "invalid_page_count",
        }[kind]
    )


def test_docx_preserves_vietnamese_table_header_and_footer():
    analysis = _analyze_docx(document(docx_bytes(), ".docx"))
    assert analysis.native_complete
    assert "Hồ sơ mẫu" in analysis.header_text
    assert analysis.footer_text == "FOOTER_MARKER"
    for term in ("Kỹ năng", "Học vấn", "Hồ sơ mẫu", "FOOTER_MARKER"):
        assert term in analysis.native_text


@pytest.mark.asyncio
async def test_mineru_docx_keeps_header_footer_when_provider_omits_them():
    import hashlib

    content = docx_bytes()
    source = document(content, ".docx")
    source.resume.checksum = hashlib.sha256(content).hexdigest()
    analysis = _analyze_docx(source)
    body = "Python backend engineer. MongoDB. Kỹ năng. Học vấn. " * 4

    def respond(request):
        if request.url.path == "/v1/uploads":
            return httpx.Response(200, json={
                "id": "upload", "status": "completed", "file": {"id": "source"},
            })
        if request.url.path == "/v1/parse/jobs":
            return httpx.Response(200, json={
                "job_id": "job", "status": "completed", "files": [{
                    "status": "completed", "output_files": {"markdown": {"file_id": "md"}},
                }],
            })
        if request.url.path == "/v1/files/md/content":
            return httpx.Response(200, text=body)
        raise AssertionError(request.url.path)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        output = await MinerUAdapter(client, "http://mineru:8000").parse_document(
            source, analysis, tier="flash", ocr_mode=None,
        )
    text = output.parsed_data.raw_text
    assert body.strip() in text
    assert text.startswith(analysis.header_text)
    assert text.endswith("FOOTER_MARKER")
    assert output.provider == "mineru"
    assert output.parsed_data.parser_version == "mineru-v1-flash-native+docx-margins"
    assert output.parsed_data.skills == []


def test_docx_margins_already_in_markdown_are_not_duplicated():
    analysis = _analyze_docx(document(docx_bytes(), ".docx"))
    text = f"**{analysis.header_text}**\n\nPython engineer\n\nFOOTER\\_MARKER"
    restored, changed = _restore_docx_margins(text, analysis)
    assert not changed
    assert restored == text


def test_docx_margin_restoration_cannot_bypass_text_limit():
    analysis = _analyze_docx(document(docx_bytes(), ".docx"))
    with pytest.raises(ProviderResponseError) as caught:
        _restore_docx_margins("x" * 1_000_000, analysis)
    assert caught.value.code == "mineru_markdown_too_large"


def test_markdown_images_and_urls_are_not_cv_content():
    text = "\n".join(f"![](https://host/images/{'a' * 100}{i}.png)" for i in range(10))
    assert not evaluate_text_quality(text).accepted
    assert evaluate_text_quality(
        "\n".join(["<table><tr><td>Python engineer experience</td></tr></table>"] * 10)
    ).accepted


@pytest.mark.asyncio
async def test_real_child_analysis_and_invalid_document():
    result = await analyze_document(document(digital_pdf()))
    assert "Python" in result.native_text and not result.requires_ocr
    with pytest.raises(PermanentParseError) as caught:
        await analyze_document(document(b"invalid PDF"))
    assert caught.value.code == "invalid_pdf"


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_inspection_timeout_and_cancellation_reap_child(monkeypatch, cancel):
    started = asyncio.Event()
    process = SimpleNamespace(returncode=None)

    async def communicate(*args):
        if process.returncode is not None:
            return b"", None
        started.set()
        await asyncio.Event().wait()

    def kill():
        process.returncode = -9

    process.kill = kill
    process.communicate = AsyncMock(side_effect=communicate)
    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", AsyncMock(return_value=process)
    )
    monkeypatch.setattr(settings, "PARSE_PREFLIGHT_TIMEOUT_SECONDS", 0.02)
    task = asyncio.create_task(analyze_document(document(b"pdf")))
    await started.wait()
    if cancel:
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else PermanentParseError):
        await task
    assert process.returncode == -9
    assert process.communicate.await_count == 2


@pytest.mark.asyncio
async def test_broken_http_protocol_is_retryable():
    def broken(request):
        raise httpx.RemoteProtocolError("sensitive server details")

    async with httpx.AsyncClient(transport=httpx.MockTransport(broken)) as client:
        with pytest.raises(TransientParseError) as caught:
            await MinerUAdapter(client, "http://mineru:8000")._json(
                "GET", "http://mineru:8000/v1/health"
            )
    assert caught.value.code == "mineru_connection_failed"


@pytest.mark.asyncio
async def test_malformed_file_result_uses_provider_error():
    from app.workers.document import DocumentAnalysis

    doc = document(digital_pdf())
    doc.resume.checksum = "a" * 64
    responses = iter(
        [
            {"id": "u", "status": "completed", "file": {"id": "f"}},
            {"job_id": "j", "status": "completed", "files": [None]},
        ]
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=next(responses))
        )
    ) as client:
        with pytest.raises(ProviderResponseError) as caught:
            await MinerUAdapter(client, "http://mineru:8000").parse_document(
                doc,
                DocumentAnalysis(1, "text", 1, False),
                tier="basic",
                ocr_mode="txt",
            )
    assert caught.value.code == "mineru_result_incomplete"
