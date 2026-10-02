import hashlib
import json
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from bson import ObjectId
from pypdf import PdfWriter

from app.core.config import settings
from app.models.resume_file import ResumeFile
from app.workers.mineru_adapter import MinerUAdapter
from app.workers.errors import PermanentParseError, TransientParseError


@pytest.fixture
def resume(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "UPLOAD_BASE_DIR", tmp_path)
    folder = tmp_path / "resumes"
    folder.mkdir()
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    stream = BytesIO()
    writer.write(stream)
    content = stream.getvalue()
    path = folder / "cv.pdf"
    path.write_bytes(content)
    record = SimpleNamespace(
        file_path=str(path), file_size=len(content), checksum=hashlib.sha256(content).hexdigest(),
        mime_type="application/pdf", storage_provider="local",
    )
    run = SimpleNamespace(resume_file_id=ObjectId(), company_id=ObjectId())
    return record, run, content


@pytest.mark.asyncio
async def test_mineru_full_cycle_with_scanned_pdf_tier(resume, monkeypatch):
    record, run, content = resume
    monkeypatch.setattr(ResumeFile, "find_one", AsyncMock(return_value=record))
    requests = []

    def respond(request):
        requests.append(request)
        path = request.url.path
        if path == "/v1/uploads" and request.method == "POST":
            assert request.content
            assert b'"sha256sum"' in request.content
            return httpx.Response(200, json={"id": "u1", "status": "pending", "upload_url": "/v1/uploads/u1/content", "upload_method": "PUT", "upload_headers": {"Content-Type": "application/pdf"}})
        if path == "/v1/uploads/u1/content":
            assert request.content == content
            return httpx.Response(200)
        if path == "/v1/uploads/u1/complete":
            return httpx.Response(200, json={"status": "completed", "file": {"id": "f1"}})
        if path == "/v1/parse/jobs" and request.method == "POST":
            payload = json.loads(request.content)
            assert payload["tier"] == "basic"
            assert payload["ocr_mode"] == "ocr"
            return httpx.Response(200, json={"job_id": "j1", "status": "completed", "files": [{"status": "completed", "output_files": {"markdown": {"file_id": "md1"}}}]})
        if path == "/v1/files/md1/content":
            return httpx.Response(200, content="Nguyễn Văn A\nPython".encode())
        raise AssertionError(path)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond), headers={"Authorization": "Bearer test"}) as client:
        output = await MinerUAdapter(client, "http://mineru:8000").parse(run)
    assert output.parsed_data.raw_text == "Nguyễn Văn A\nPython"
    assert output.parsed_data.skills == []
    assert output.parsed_data.parser_version == "mineru-v1-basic-ocr"
    assert len(requests) == 5
    ResumeFile.find_one.assert_awaited_once_with({"_id": run.resume_file_id, "company_id": run.company_id, "is_deleted": False})


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", ["outside", "changed", "cross_origin", "missing_artifact", "oversize"])
async def test_mineru_rejects_unsafe_or_incomplete_inputs(resume, monkeypatch, tmp_path, problem):
    record, run, _ = resume
    monkeypatch.setattr(ResumeFile, "find_one", AsyncMock(return_value=record))
    if problem == "outside":
        record.file_path = str(tmp_path / "outside.pdf")
        (tmp_path / "outside.pdf").write_bytes(b"%PDF-1.7")
    elif problem == "changed":
        record.checksum = "0" * 64

    def respond(request):
        path = request.url.path
        if path == "/v1/uploads":
            url = "http://attacker.test/upload" if problem == "cross_origin" else "/v1/upload-bytes"
            return httpx.Response(200, json={"id": "u", "status": "pending", "upload_url": url})
        if path == "/v1/upload-bytes":
            return httpx.Response(200)
        if path == "/v1/uploads/u/complete":
            return httpx.Response(200, json={"status": "completed", "file": {"id": "f"}})
        if path == "/v1/parse/jobs":
            artifact = {} if problem == "missing_artifact" else {"markdown": {"file_id": "m"}}
            return httpx.Response(200, json={"job_id": "j", "status": "completed", "files": [{"status": "completed", "output_files": artifact}]})
        if path == "/v1/files/m/content":
            return httpx.Response(200, content=b"x" * 1_000_001)
        raise AssertionError(path)

    if problem in {"missing_artifact", "oversize"}:
        # Use a valid same-origin create response to reach the later stage.
        original = respond
        def respond(request):
            if request.url.path == "/v1/uploads":
                return httpx.Response(200, json={"id": "u", "status": "pending", "upload_url": "/v1/upload-bytes"})
            return original(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(ValueError):
            await MinerUAdapter(client, "http://mineru:8000").parse(run)


@pytest.mark.asyncio
async def test_mineru_deduplicated_upload_and_poll(resume, monkeypatch):
    record, run, _ = resume
    monkeypatch.setattr(ResumeFile, "find_one", AsyncMock(return_value=record))
    monkeypatch.setattr(settings, "MINERU_POLL_INTERVAL_SECONDS", 0.01)
    polls = 0

    def respond(request):
        nonlocal polls
        path = request.url.path
        if path == "/v1/uploads":
            return httpx.Response(200, json={"id": "u", "status": "completed", "file": {"id": "f"}})
        if path == "/v1/parse/jobs" and request.method == "POST":
            return httpx.Response(200, json={"job_id": "j", "status": "queued"})
        if path == "/v1/parse/jobs/j":
            polls += 1
            return httpx.Response(200, json={"status": "completed", "files": [{"status": "completed", "output_files": {"markdown": {"file_id": "m"}}}]})
        if path == "/v1/files/m/content":
            return httpx.Response(200, text="CV")
        raise AssertionError(path)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await MinerUAdapter(client, "http://mineru:8000").parse(run)
    assert result.parsed_data.raw_text == "CV"
    assert polls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("lost_stage", ["upload_bytes", "complete", "poll", "download"])
async def test_restart_losing_resource_allows_fresh_upload_cycle(resume, monkeypatch, lost_stage):
    record, run, _ = resume
    monkeypatch.setattr(ResumeFile, "find_one", AsyncMock(return_value=record))
    monkeypatch.setattr(settings, "MINERU_POLL_INTERVAL_SECONDS", 0.001)
    uploads = 0

    def respond(request):
        nonlocal uploads
        path = request.url.path
        if path == "/v1/uploads":
            uploads += 1
            return httpx.Response(200, json={
                "id": f"u{uploads}", "status": "pending",
                "upload_url": f"/v1/uploads/u{uploads}/content",
            })
        stages = {
            f"/v1/uploads/u{uploads}/content": "upload_bytes",
            f"/v1/uploads/u{uploads}/complete": "complete",
            f"/v1/parse/jobs/j{uploads}": "poll",
            f"/v1/files/m{uploads}/content": "download",
        }
        stage = stages.get(path)
        if uploads == 1 and stage == lost_stage:
            return httpx.Response(404, json={"detail": "resource not found"})
        if stage == "upload_bytes":
            return httpx.Response(200)
        if stage == "complete":
            return httpx.Response(200, json={"status": "completed", "file": {"id": f"f{uploads}"}})
        if path == "/v1/parse/jobs":
            return httpx.Response(200, json={"job_id": f"j{uploads}", "status": "queued"})
        if stage == "poll":
            return httpx.Response(200, json={"status": "completed", "files": [{
                "status": "completed", "output_files": {"markdown": {"file_id": f"m{uploads}"}},
            }]})
        if stage == "download":
            return httpx.Response(200, text="Python backend engineer " * 30)
        raise AssertionError(path)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        adapter = MinerUAdapter(client, "http://mineru:8000")
        with pytest.raises(TransientParseError) as caught:
            await adapter.parse(run)
        assert caught.value.code == "mineru_resource_lost"
        result = await adapter.parse(run)
    assert uploads == 2
    assert result.parsed_data.raw_text.startswith("Python backend engineer")


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["uploads", "parse/jobs"])
async def test_unknown_create_endpoint_is_not_treated_as_lost_resource(endpoint):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(404))
    ) as client:
        adapter = MinerUAdapter(client, "http://mineru:8000")
        with pytest.raises(PermanentParseError) as caught:
            await adapter._json("POST", adapter._url(endpoint), json={})
    assert caught.value.retryable is False
