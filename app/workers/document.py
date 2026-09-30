import asyncio
import hashlib
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from docx import Document as DocxDocument
from pypdf import PdfReader

from app.core.config import settings
from app.models.resume_file import ResumeFile
from app.models.screening_run import ResumeParseRun
from app.workers.errors import PermanentParseError, UnsafeDocumentError


SUPPORTED_MIME_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
MAX_EXTRACTED_CHARACTERS = 1_000_000


@dataclass(frozen=True)
class ResumeDocument:
    resume: ResumeFile
    content: bytes
    extension: str
    filename: str


@dataclass(frozen=True)
class DocumentAnalysis:
    page_count: int
    native_text: str
    text_page_ratio: float
    requires_ocr: bool


def _read_local_resume(resume: ResumeFile) -> ResumeDocument:
    if resume.storage_provider != "local":
        raise PermanentParseError(
            "Only local resume storage is supported",
            code="unsupported_storage_provider",
        )
    try:
        root = settings.resume_upload_path.resolve()
        path = Path(resume.file_path).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise UnsafeDocumentError("Resume file cannot be resolved") from exc
    extension = path.suffix.lower()
    if not path.is_relative_to(root) or extension not in SUPPORTED_MIME_TYPES:
        raise UnsafeDocumentError("Resume path or format is invalid")
    if resume.mime_type != SUPPORTED_MIME_TYPES[extension]:
        raise UnsafeDocumentError("Resume MIME type does not match its extension")
    if not 0 < resume.file_size <= settings.MAX_RESUME_SIZE:
        raise UnsafeDocumentError("Resume size metadata is invalid")
    try:
        with path.open("rb") as handle:
            content = handle.read(settings.MAX_RESUME_SIZE + 1)
    except OSError as exc:
        raise UnsafeDocumentError("Resume file cannot be read") from exc
    if len(content) != resume.file_size:
        raise UnsafeDocumentError("Resume size does not match stored metadata")
    if hashlib.sha256(content).hexdigest() != resume.checksum:
        raise UnsafeDocumentError("Resume checksum does not match stored metadata")
    return ResumeDocument(
        resume=resume,
        content=content,
        extension=extension,
        filename=path.name,
    )


async def load_resume_document(run: ResumeParseRun) -> ResumeDocument:
    resume = await ResumeFile.find_one(
        {
            "_id": run.resume_file_id,
            "company_id": run.company_id,
            "is_deleted": False,
        }
    )
    if resume is None:
        raise PermanentParseError(
            "Resume is missing or belongs to another company",
            code="resume_not_found",
        )
    return await asyncio.to_thread(_read_local_resume, resume)


def _truncate(parts: list[str]) -> str:
    return "\n\n".join(part.strip() for part in parts if part.strip())[
        :MAX_EXTRACTED_CHARACTERS
    ]


def _analyze_pdf(document: ResumeDocument) -> DocumentAnalysis:
    try:
        reader = PdfReader(BytesIO(document.content), strict=False)
        if reader.is_encrypted:
            raise PermanentParseError(
                "Password-protected PDFs are not supported",
                code="encrypted_pdf",
            )
        page_count = len(reader.pages)
        if not 0 < page_count <= settings.MAX_RESUME_PAGES:
            raise PermanentParseError(
                "PDF page count is outside the allowed range",
                code="invalid_page_count",
            )
        page_texts = [(page.extract_text() or "").strip() for page in reader.pages]
    except PermanentParseError:
        raise
    except Exception:
        # Some valid PDFs cannot be decoded by pypdf but can still be parsed by
        # MinerU. Treat preflight as unknown and select OCR-capable parsing.
        return DocumentAnalysis(
            page_count=1,
            native_text="",
            text_page_ratio=0.0,
            requires_ocr=True,
        )

    useful_pages = sum(
        len(text) >= settings.PARSE_MIN_TEXT_CHARACTERS_PER_PAGE
        for text in page_texts
    )
    ratio = useful_pages / page_count
    return DocumentAnalysis(
        page_count=page_count,
        native_text=_truncate(page_texts),
        text_page_ratio=round(ratio, 4),
        requires_ocr=ratio < settings.PARSE_NATIVE_TEXT_PAGE_RATIO,
    )


def _analyze_docx(document: ResumeDocument) -> DocumentAnalysis:
    try:
        source = DocxDocument(BytesIO(document.content))
        parts = [paragraph.text for paragraph in source.paragraphs]
        for table in source.tables:
            for row in table.rows:
                parts.append("\t".join(cell.text for cell in row.cells))
        text = _truncate(parts)
    except Exception:
        text = ""
    return DocumentAnalysis(
        page_count=1,
        native_text=text,
        text_page_ratio=1.0 if text else 0.0,
        requires_ocr=False,
    )


async def analyze_document(document: ResumeDocument) -> DocumentAnalysis:
    analyzer = _analyze_pdf if document.extension == ".pdf" else _analyze_docx
    return await asyncio.to_thread(analyzer, document)
