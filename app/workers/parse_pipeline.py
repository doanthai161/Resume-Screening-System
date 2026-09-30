from collections.abc import Awaitable, Callable

from app.core.config import settings
from app.models.resume_parse_attempt import ParseAttemptStatus
from app.models.screening_run import ResumeParseRun
from app.schemas.worker import ParseOutput
from app.workers.circuit_breaker import MinerUCircuitBreaker
from app.workers.document import (
    DocumentAnalysis,
    ResumeDocument,
    analyze_document,
    load_resume_document,
)
from app.workers.errors import (
    CircuitOpenError,
    LowQualityParseError,
    ParseAdapterError,
    TransientParseError,
)
from app.workers.native_adapter import NativeTextAdapter
from app.workers.parse_attempts import ParseAttemptRecorder
from app.workers.quality import evaluate_text_quality


class ParsePipelineAdapter:
    """Format-aware MinerU pipeline with quality and native-text fallback."""

    def __init__(
        self,
        mineru,
        *,
        native: NativeTextAdapter | None = None,
        circuit: MinerUCircuitBreaker | None = None,
        recorder: ParseAttemptRecorder | None = None,
    ) -> None:
        self.mineru = mineru
        self.native = native or NativeTextAdapter()
        self.circuit = circuit or MinerUCircuitBreaker()
        self.recorder = recorder or ParseAttemptRecorder()
        self.sequence = 0

    async def _attempt(
        self,
        run: ResumeParseRun,
        analysis: DocumentAnalysis,
        *,
        provider: str,
        mode: str | None,
        tier: str | None,
        operation: Callable[[], Awaitable[ParseOutput]],
    ) -> ParseOutput:
        self.sequence += 1
        attempt, started = await self.recorder.start(
            run,
            sequence=self.sequence,
            provider=provider,
            mode=mode,
            tier=tier,
            page_count=analysis.page_count,
        )
        try:
            output = await operation()
        except ParseAdapterError as exc:
            await self.recorder.finish(
                attempt,
                started,
                status=ParseAttemptStatus.FAILED,
                error_code=exc.code,
            )
            raise
        except Exception:
            await self.recorder.finish(
                attempt,
                started,
                status=ParseAttemptStatus.FAILED,
                error_code="parser_unexpected_error",
            )
            raise

        quality = evaluate_text_quality(
            output.parsed_data.raw_text,
            analysis.page_count,
        )
        output.quality_score = quality.score
        output.parsed_data.confidence_score = quality.score
        if not quality.accepted:
            await self.recorder.finish(
                attempt,
                started,
                status=ParseAttemptStatus.REJECTED,
                quality=quality,
                error_code=quality.reason,
            )
            raise LowQualityParseError(
                "Parsed text did not meet the quality threshold",
                code=quality.reason or "parser_low_quality",
            )
        await self.recorder.finish(
            attempt,
            started,
            status=ParseAttemptStatus.COMPLETED,
            quality=quality,
        )
        return output

    async def _native_fallback(
        self,
        run: ResumeParseRun,
        document: ResumeDocument,
        analysis: DocumentAnalysis,
        reason: str,
    ) -> ParseOutput:
        provider = "native_pdf" if document.extension == ".pdf" else "native_docx"
        return await self._attempt(
            run,
            analysis,
            provider=provider,
            mode="text-layer",
            tier=None,
            operation=lambda: self.native.parse_document(
                document,
                analysis,
                fallback_reason=reason,
            ),
        )

    async def _mineru_attempt(
        self,
        run: ResumeParseRun,
        document: ResumeDocument,
        analysis: DocumentAnalysis,
        *,
        tier: str,
        mode: str | None,
        fallback_reason: str | None,
    ) -> ParseOutput:
        async def execute() -> ParseOutput:
            output = await self.mineru.parse_document(
                document,
                analysis,
                tier=tier,
                ocr_mode=mode,
                fallback_reason=fallback_reason,
            )
            output.fallback_reason = fallback_reason
            return output

        return await self._attempt(
            run,
            analysis,
            provider="mineru",
            mode=mode,
            tier=tier,
            operation=execute,
        )

    async def parse(self, run: ResumeParseRun) -> ParseOutput:
        self.sequence = 0
        document = await load_resume_document(run)
        analysis = await analyze_document(document)

        if await self.circuit.is_open():
            try:
                return await self._native_fallback(
                    run, document, analysis, "mineru_circuit_open"
                )
            except LowQualityParseError as exc:
                raise CircuitOpenError(
                    "MinerU circuit is open and no native fallback is usable",
                ) from exc

        tier = settings.MINERU_PDF_TIER if document.extension == ".pdf" else "flash"
        mode = None
        if document.extension == ".pdf":
            mode = "ocr" if settings.MINERU_AUTO_OCR and analysis.requires_ocr else "txt"

        primary_error: ParseAdapterError | None = None
        try:
            output = await self._mineru_attempt(
                run,
                document,
                analysis,
                tier=tier,
                mode=mode,
                fallback_reason=None,
            )
            await self.circuit.record_success()
            return output
        except TransientParseError as exc:
            await self.circuit.record_transient_failure()
            primary_error = exc
        except ParseAdapterError as exc:
            if not exc.fallback_allowed:
                raise
            primary_error = exc

        reason = primary_error.code
        should_retry_mineru = not isinstance(primary_error, TransientParseError) and (
            document.extension == ".pdf"
            and (mode != "ocr" or tier != settings.MINERU_FALLBACK_TIER)
        )
        if should_retry_mineru:
            try:
                output = await self._mineru_attempt(
                    run,
                    document,
                    analysis,
                    tier=settings.MINERU_FALLBACK_TIER,
                    mode="ocr",
                    fallback_reason=reason,
                )
                await self.circuit.record_success()
                return output
            except TransientParseError as exc:
                await self.circuit.record_transient_failure()
                primary_error = exc
                reason = exc.code
            except ParseAdapterError as exc:
                if not exc.fallback_allowed:
                    raise
                reason = exc.code

        try:
            return await self._native_fallback(run, document, analysis, reason)
        except LowQualityParseError as exc:
            if isinstance(primary_error, TransientParseError):
                raise primary_error from exc
            raise LowQualityParseError(
                "All document parsing strategies failed quality validation",
                code="parser_fallback_exhausted",
            ) from exc

    async def aclose(self) -> None:
        await self.mineru.aclose()
