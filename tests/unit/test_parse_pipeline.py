from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from bson import ObjectId

from app.core.config import settings
from app.models.resume_file import ParsedResumeData
from app.models.resume_parse_attempt import ParseAttemptStatus
from app.schemas.worker import ParseOutput
from app.workers.document import DocumentAnalysis, ResumeDocument
from app.workers.circuit_breaker import MinerUCircuitBreaker
from app.workers.errors import CircuitOpenError, LowQualityParseError, PermanentParseError, TransientParseError
from app.workers.native_adapter import NativeTextAdapter
from app.workers.parse_pipeline import ParsePipelineAdapter

pytestmark = pytest.mark.asyncio


class FakeRecorder:
    def __init__(self):
        self.started = []
        self.finished = []

    async def start(self, run, **data):
        handle = SimpleNamespace(id=ObjectId(), **data)
        self.started.append(data)
        return handle, 0.0

    async def finish(self, attempt, started, **data):
        self.finished.append(data)


class FakeCircuit:
    def __init__(self, opened=False):
        self.opened = opened
        self.failures = 0
        self.successes = 0

    async def is_open(self):
        return self.opened

    async def record_transient_failure(self):
        self.failures += 1

    async def record_success(self):
        self.successes += 1


def output(text, *, mode="txt"):
    return ParseOutput(
        parsed_data=ParsedResumeData(raw_text=text, parser_version=f"test-{mode}"),
        provider="mineru",
        ocr_used=mode == "ocr",
    )


@pytest.fixture
def parse_context(monkeypatch):
    run = SimpleNamespace(id=ObjectId(), company_id=ObjectId(), attempt=1)
    document = ResumeDocument(
        resume=SimpleNamespace(checksum="a" * 64),
        content=b"pdf",
        extension=".pdf",
        filename="resume.pdf",
    )

    async def load(_):
        return document

    monkeypatch.setattr("app.workers.parse_pipeline.load_resume_document", load)
    return run, document


async def test_low_quality_digital_parse_retries_with_ocr_and_fallback_tier(
    parse_context, monkeypatch
):
    run, _ = parse_context
    analysis = DocumentAnalysis(
        page_count=1,
        native_text="Native " * 30,
        text_page_ratio=1.0,
        requires_ocr=False,
    )

    async def analyze(_):
        return analysis

    monkeypatch.setattr("app.workers.parse_pipeline.analyze_document", analyze)
    mineru = SimpleNamespace(parse_document=AsyncMock(), aclose=AsyncMock())
    mineru.parse_document.side_effect = [output("x"), output("OCR result " * 30, mode="ocr")]
    recorder = FakeRecorder()
    circuit = FakeCircuit()
    pipeline = ParsePipelineAdapter(mineru, recorder=recorder, circuit=circuit)

    result = await pipeline.parse(run)

    assert result.provider == "mineru"
    assert result.ocr_used is True
    assert result.fallback_reason == "text_too_short"
    assert mineru.parse_document.await_args_list[0].kwargs["ocr_mode"] == "txt"
    assert mineru.parse_document.await_args_list[1].kwargs["ocr_mode"] == "ocr"
    assert recorder.started[1]["tier"] == "standard"
    assert recorder.finished[0]["status"] == ParseAttemptStatus.REJECTED
    assert recorder.finished[1]["status"] == ParseAttemptStatus.COMPLETED
    assert circuit.successes == 1


async def test_transient_mineru_failure_uses_native_text_fallback(parse_context, monkeypatch):
    run, _ = parse_context
    analysis = DocumentAnalysis(
        page_count=1,
        native_text="Python backend engineer " * 20,
        text_page_ratio=1.0,
        requires_ocr=False,
    )

    async def analyze(_):
        return analysis

    monkeypatch.setattr("app.workers.parse_pipeline.analyze_document", analyze)
    mineru = SimpleNamespace(
        parse_document=AsyncMock(side_effect=TransientParseError(code="mineru_http_503")),
        aclose=AsyncMock(),
    )
    circuit = FakeCircuit()
    pipeline = ParsePipelineAdapter(mineru, recorder=FakeRecorder(), circuit=circuit)

    result = await pipeline.parse(run)

    assert result.provider == "native_pdf"
    assert result.fallback_reason == "mineru_http_503"
    assert circuit.failures == 1
    assert mineru.parse_document.await_count == 1


async def test_open_circuit_with_scanned_pdf_remains_retryable(parse_context, monkeypatch):
    run, _ = parse_context

    async def analyze(_):
        return DocumentAnalysis(
            page_count=2,
            native_text="",
            text_page_ratio=0.0,
            requires_ocr=True,
        )

    monkeypatch.setattr("app.workers.parse_pipeline.analyze_document", analyze)
    mineru = SimpleNamespace(parse_document=AsyncMock(), aclose=AsyncMock())
    pipeline = ParsePipelineAdapter(
        mineru,
        recorder=FakeRecorder(),
        circuit=FakeCircuit(opened=True),
    )

    with pytest.raises(CircuitOpenError) as caught:
        await pipeline.parse(run)

    assert caught.value.code == "mineru_circuit_open"
    mineru.parse_document.assert_not_awaited()


@pytest.mark.parametrize("ratio,requires_ocr", [(0.1, True), (0.9, False), (1.0, True)])
async def test_native_pdf_rejects_incomplete_coverage_despite_long_text(
    parse_context, ratio, requires_ocr
):
    _, document = parse_context
    analysis = DocumentAnalysis(10, "Python engineer " * 300, ratio, requires_ocr)
    with pytest.raises(LowQualityParseError) as caught:
        await NativeTextAdapter().parse_document(document, analysis)
    assert caught.value.code == "native_pdf_incomplete_coverage"


async def test_mixed_pdf_preserves_retryable_provider_error(parse_context, monkeypatch):
    run, _ = parse_context
    monkeypatch.setattr(
        "app.workers.parse_pipeline.analyze_document",
        AsyncMock(return_value=DocumentAnalysis(10, "Python engineer " * 300, 0.1, True)),
    )
    mineru = SimpleNamespace(
        parse_document=AsyncMock(side_effect=TransientParseError(code="mineru_http_503")),
        aclose=AsyncMock(),
    )
    pipeline = ParsePipelineAdapter(mineru, recorder=FakeRecorder(), circuit=FakeCircuit())
    with pytest.raises(TransientParseError) as caught:
        await pipeline.parse(run)
    assert caught.value.code == "mineru_http_503"


async def test_docx_native_fallback_is_not_subject_to_pdf_coverage(parse_context):
    _, source = parse_context
    document = ResumeDocument(source.resume, b"docx", ".docx", "cv.docx")
    result = await NativeTextAdapter().parse_document(
        document, DocumentAnalysis(1, "Python engineer " * 30, 0.0, False)
    )
    assert result.provider == "native_docx"


async def test_permanent_mineru_rejection_does_not_fallback(parse_context, monkeypatch):
    run, _ = parse_context

    async def analyze(_):
        return DocumentAnalysis(1, "Native " * 30, 1.0, False)

    monkeypatch.setattr("app.workers.parse_pipeline.analyze_document", analyze)
    error = PermanentParseError(code="mineru_http_400")
    mineru = SimpleNamespace(parse_document=AsyncMock(side_effect=error), aclose=AsyncMock())
    pipeline = ParsePipelineAdapter(
        mineru,
        recorder=FakeRecorder(),
        circuit=FakeCircuit(),
    )

    with pytest.raises(PermanentParseError) as caught:
        await pipeline.parse(run)

    assert caught.value.code == "mineru_http_400"
    assert mineru.parse_document.await_count == 1


async def test_redis_circuit_opens_after_transient_failure_threshold(monkeypatch):
    class FakeRedis:
        def __init__(self):
            self.values = {}

        async def exists(self, key):
            return int(key in self.values)

        async def incr(self, key):
            self.values[key] = int(self.values.get(key, 0)) + 1
            return self.values[key]

        async def expire(self, key, ttl):
            return True

        async def setex(self, key, ttl, value):
            self.values[key] = value

        async def delete(self, *keys):
            for key in keys:
                self.values.pop(key, None)

    redis = FakeRedis()
    monkeypatch.setattr("app.workers.circuit_breaker.get_redis", lambda: redis)
    monkeypatch.setattr(settings, "MINERU_CIRCUIT_FAILURE_THRESHOLD", 2)
    circuit = MinerUCircuitBreaker()

    await circuit.record_transient_failure()
    assert not await circuit.is_open()
    await circuit.record_transient_failure()
    assert await circuit.is_open()
    await circuit.record_success()
    assert not await circuit.is_open()
