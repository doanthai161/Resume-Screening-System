import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from bson import ObjectId

from app.core.config import settings
from app.models.resume_file import ResumeFile
from app.workers.mineru_adapter import MinerUAdapter


@pytest.fixture
def resume(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "UPLOAD_BASE_DIR", tmp_path)
    folder = tmp_path / "resumes"
    folder.mkdir()
    content = b"%PDF-1.7\nexample"
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
            assert b'"tier":"basic"' in request.content
            return httpx.Response(200, json={"job_id": "j1", "status": "completed", "files": [{"status": "completed", "output_files": {"markdown": {"file_id": "md1"}}}]})
        if path == "/v1/files/md1/content":
            return httpx.Response(200, content="Nguyễn Văn A\nPython".encode())
        raise AssertionError(path)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond), headers={"Authorization": "Bearer test"}) as client:
        output = await MinerUAdapter(client, "http://mineru:8000").parse(run)
    assert output.parsed_data.raw_text == "Nguyễn Văn A\nPython"
    assert output.parsed_data.skills == []
    assert output.parsed_data.parser_version == "mineru-v1-basic"
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
