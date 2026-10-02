from app.models.resume_file import ParsedResumeData
from app.models.screening_run import ResumeParseRun
from app.schemas.worker import ParseOutput
from app.workers.document import (
    DocumentAnalysis,
    ResumeDocument,
    analyze_document,
    load_resume_document,
)
from app.workers.errors import LowQualityParseError
from app.workers.quality import evaluate_text_quality


class NativeTextAdapter:
    async def parse_document(
        self,
        document: ResumeDocument,
        analysis: DocumentAnalysis,
        *,
        fallback_reason: str | None = None,
    ) -> ParseOutput:
        # A long text layer on one page cannot compensate for missing pages.
        # Be conservative even when the OCR routing threshold accepts a mixed PDF.
        if document.extension == ".pdf" and (
            analysis.requires_ocr or analysis.text_page_ratio < 1.0
        ):
            raise LowQualityParseError(
                "Native extraction does not cover every PDF page",
                code="native_pdf_incomplete_coverage",
            )
        if not analysis.native_complete:
            raise LowQualityParseError(
                "Native extraction cannot cover embedded document content",
                code="native_docx_incomplete_coverage",
            )
        quality = evaluate_text_quality(analysis.native_text, analysis.page_count)
        if not quality.accepted:
            raise LowQualityParseError(
                "Native text extraction did not meet the quality threshold",
                code=quality.reason or "native_text_low_quality",
            )
        provider = "native_pdf" if document.extension == ".pdf" else "native_docx"
        return ParseOutput(
            parsed_data=ParsedResumeData(
                raw_text=analysis.native_text,
                parser_version=f"{provider}-v1",
                confidence_score=quality.score,
            ),
            provider=provider,
            ocr_used=False,
            quality_score=quality.score,
            fallback_reason=fallback_reason,
        )

    async def parse(self, run: ResumeParseRun) -> ParseOutput:
        document = await load_resume_document(run)
        analysis = await analyze_document(document)
        return await self.parse_document(document, analysis)

    async def aclose(self) -> None:
        return None
