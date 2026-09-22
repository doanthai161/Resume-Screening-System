import asyncio
import hashlib
import os
import uuid
import zipfile
from pathlib import Path
from typing import Optional

from beanie import PydanticObjectId
from fastapi import UploadFile, status
from pymongo.errors import DuplicateKeyError
from bson import ObjectId

from app.core import cache
from app.core import job_queue
from app.core.config import settings
from app.core.errors import CustomError, ErrorCodes
from app.core.security import CurrentUser
from app.models.ai_model import AIModel
from app.models.candidate import Candidate
from app.models.company_branch import CompanyBranch
from app.models.company import Company
from app.models.resume_file import ResumeFile
from app.models.screening_run import ResumeParseRun
from app.schemas.resume import ParseResumeRequest
from app.services.tenant_access_service import TenantAccessService
from app.utils.time import now_utc


FILE_SIGNATURES = {
    "pdf": (b"%PDF-",),
    "docx": (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"),
    "doc": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",),
}


def _matches_signature(extension: str, header: bytes) -> bool:
    signatures = FILE_SIGNATURES.get(extension, ())
    return any(header.startswith(signature) for signature in signatures)


def _validate_docx_archive(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > settings.MAX_DOCX_ENTRIES:
                return False
            names = {entry.filename.replace("\\", "/") for entry in entries}
            if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                return False
            total_size = 0
            total_compressed = 0
            for entry in entries:
                normalized = entry.filename.replace("\\", "/")
                if normalized.startswith("/") or ".." in Path(normalized).parts:
                    return False
                total_size += entry.file_size
                total_compressed += max(entry.compress_size, 1)
            if total_size > settings.MAX_DOCX_UNCOMPRESSED_SIZE:
                return False
            if total_size > 10 * 1024 * 1024 and total_size / total_compressed > 100:
                return False
            return True
    except (OSError, zipfile.BadZipFile, RuntimeError):
        return False


class ResumeService:
    @staticmethod
    async def upload(
        company_id: str,
        candidate_id: str,
        company_branch_id: Optional[str],
        file: UploadFile,
        current_user: CurrentUser,
    ) -> ResumeFile:
        if not ObjectId.is_valid(company_id):
            raise CustomError(ErrorCodes.VALIDATION, "Invalid company ID", status.HTTP_422_UNPROCESSABLE_ENTITY)
        if not ObjectId.is_valid(candidate_id):
            raise CustomError(ErrorCodes.VALIDATION, "Invalid candidate ID", status.HTTP_422_UNPROCESSABLE_ENTITY)
        if company_branch_id and not ObjectId.is_valid(company_branch_id):
            raise CustomError(ErrorCodes.VALIDATION, "Invalid company branch ID", status.HTTP_422_UNPROCESSABLE_ENTITY)
        candidate = await Candidate.find_one(
            {
                "_id": PydanticObjectId(candidate_id),
                "company_id": PydanticObjectId(company_id),
                "is_deleted": False,
            }
        )
        if not candidate:
            raise CustomError(ErrorCodes.NOT_FOUND, "Candidate not found", status.HTTP_404_NOT_FOUND)
        company = await Company.get(PydanticObjectId(company_id))
        if not company or not company.is_active:
            raise CustomError(ErrorCodes.NOT_FOUND, "Company not found", status.HTTP_404_NOT_FOUND)

        is_own_candidate = (
            current_user.is_candidate
            and not current_user.is_recruiter
            and not current_user.is_admin
            and candidate.user_id is not None
            and str(candidate.user_id) == current_user.user_id
        )
        if not is_own_candidate:
            await TenantAccessService.require_company_access(current_user, company_id)

        branch_id = None
        if company_branch_id:
            branch = await CompanyBranch.find_one(
                {
                    "_id": PydanticObjectId(company_branch_id),
                    "company_id": PydanticObjectId(company_id),
                    "is_active": True,
                }
            )
            if not branch:
                raise CustomError(ErrorCodes.NOT_FOUND, "Company branch not found", status.HTTP_404_NOT_FOUND)
            branch_id = branch.id

        original_name = Path((file.filename or "resume").replace("\\", "/")).name
        if len(original_name) > 255:
            raise CustomError(ErrorCodes.BAD_REQUEST, "Resume filename is too long", status.HTTP_400_BAD_REQUEST)
        extension = Path(original_name).suffix.lower().lstrip(".")
        if extension not in settings.allowed_resume_extensions_list:
            raise CustomError(ErrorCodes.BAD_REQUEST, "Unsupported resume extension", status.HTTP_400_BAD_REQUEST)
        if file.content_type not in settings.allowed_resume_mime_types:
            raise CustomError(ErrorCodes.BAD_REQUEST, "Unsupported resume MIME type", status.HTTP_400_BAD_REQUEST)

        settings.temp_upload_path.mkdir(parents=True, exist_ok=True)
        settings.resume_upload_path.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        temp_path = settings.temp_upload_path / f"{token}.upload"
        final_name = f"{token}.{extension}"
        final_path = settings.resume_upload_path / final_name
        digest = hashlib.sha256()
        total_size = 0
        header = b""

        try:
            with open(temp_path, "xb") as handle:
                while True:
                    chunk = await file.read(settings.UPLOAD_CHUNK_SIZE)
                    if not chunk:
                        break
                    total_size += len(chunk)
                    if total_size > settings.MAX_RESUME_SIZE:
                        raise CustomError(
                            ErrorCodes.BAD_REQUEST,
                            "Resume exceeds maximum upload size",
                            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        )
                    if len(header) < 16:
                        header += chunk[: 16 - len(header)]
                    digest.update(chunk)
                    await asyncio.to_thread(handle.write, chunk)

            if total_size == 0:
                raise CustomError(ErrorCodes.BAD_REQUEST, "Resume file is empty", status.HTTP_400_BAD_REQUEST)
            if not _matches_signature(extension, header):
                raise CustomError(
                    ErrorCodes.BAD_REQUEST,
                    "File content does not match its extension",
                    status.HTTP_400_BAD_REQUEST,
                )
            if extension == "docx" and not await asyncio.to_thread(
                _validate_docx_archive, temp_path
            ):
                raise CustomError(
                    ErrorCodes.BAD_REQUEST,
                    "Invalid or unsafe DOCX archive",
                    status.HTTP_400_BAD_REQUEST,
                )

            checksum = digest.hexdigest()
            existing = await ResumeFile.find_one(
                {
                    "company_id": PydanticObjectId(company_id),
                    "candidate_id": candidate.id,
                    "checksum": checksum,
                    "is_deleted": False,
                }
            )
            if existing:
                return existing

            await asyncio.to_thread(os.replace, temp_path, final_path)
            resume = ResumeFile(
                company_id=PydanticObjectId(company_id),
                candidate_id=candidate.id,
                filename=final_name,
                original_filename=original_name,
                file_path=str(final_path),
                file_size=total_size,
                mime_type=file.content_type,
                uploader_id=PydanticObjectId(current_user.user_id),
                user_id=candidate.user_id,
                company_branch_id=branch_id,
                checksum=checksum,
                storage_provider="local",
                object_key=final_name,
            )
            try:
                await resume.insert()
                return resume
            except DuplicateKeyError:
                duplicate = await ResumeFile.find_one(
                    {
                        "company_id": PydanticObjectId(company_id),
                        "candidate_id": candidate.id,
                        "checksum": checksum,
                        "is_deleted": False,
                    }
                )
                if duplicate:
                    await asyncio.to_thread(final_path.unlink, True)
                    return duplicate
                raise
            except Exception:
                if final_path.exists():
                    await asyncio.to_thread(final_path.unlink, True)
                raise
        finally:
            await file.close()
            if temp_path.exists():
                await asyncio.to_thread(temp_path.unlink, True)

    @staticmethod
    async def start_parse(
        resume_id: str,
        data: ParseResumeRequest,
        idempotency_key: str,
        current_user: CurrentUser,
    ) -> ResumeParseRun:
        await TenantAccessService.require_company_access(current_user, data.company_id)
        resume = await ResumeFile.find_one(
            {
                "_id": PydanticObjectId(resume_id),
                "company_id": PydanticObjectId(data.company_id),
                "is_deleted": False,
            }
        )
        if not resume:
            raise CustomError(ErrorCodes.NOT_FOUND, "Resume not found", status.HTTP_404_NOT_FOUND)

        parser_model_id = None
        if data.parser_model_id:
            model = await AIModel.find_one(
                {
                    "_id": PydanticObjectId(data.parser_model_id),
                    "model_type": "resume_parser",
                    "is_active": True,
                }
            )
            if not model:
                raise CustomError(ErrorCodes.NOT_FOUND, "Parser model not found", status.HTTP_404_NOT_FOUND)
            parser_model_id = model.id

        input_hash = hashlib.sha256(
            f"{resume.checksum}:{data.parser_version}:{parser_model_id or 'default'}".encode("utf-8")
        ).hexdigest()
        existing = await ResumeParseRun.find_one(
            {
                "company_id": PydanticObjectId(data.company_id),
                "$or": [
                    {"idempotency_key": idempotency_key},
                    {"input_hash": input_hash, "is_terminal": False},
                ],
            }
        )
        if existing:
            if existing.idempotency_key == idempotency_key and existing.input_hash != input_hash:
                raise CustomError(
                    ErrorCodes.CONFLICT,
                    "Idempotency key was already used for a different request",
                    status.HTTP_409_CONFLICT,
                )
            return existing

        owner_token = uuid.uuid4().hex
        if not await cache.reserve_idempotency("parse", data.company_id, idempotency_key, owner_token):
            raise CustomError(ErrorCodes.CONFLICT, "Parse request is already processing", status.HTTP_409_CONFLICT)
        try:
            run = ResumeParseRun(
                company_id=PydanticObjectId(data.company_id),
                resume_file_id=resume.id,
                parser_model_id=parser_model_id,
                triggered_by=PydanticObjectId(current_user.user_id),
                idempotency_key=idempotency_key,
                input_hash=input_hash,
                parser_version=data.parser_version,
            )
            await run.insert()
            message_id = await job_queue.enqueue("resume-parse", str(run.id), data.company_id)
            if message_id:
                await ResumeParseRun.find_one(
                    {"_id": run.id, "status": "queued"}
                ).update(
                    {
                        "$set": {
                            "queue_message_id": str(message_id),
                            "last_enqueued_at": now_utc(),
                            "updated_at": now_utc(),
                        }
                    }
                )
            await cache.complete_idempotency(
                "parse", data.company_id, idempotency_key, str(run.id), owner_token
            )
            return run
        except DuplicateKeyError:
            duplicate = await ResumeParseRun.find_one(
                {
                    "company_id": PydanticObjectId(data.company_id),
                    "$or": [
                        {"idempotency_key": idempotency_key},
                        {"input_hash": input_hash, "is_terminal": False},
                    ],
                }
            )
            if duplicate:
                if duplicate.idempotency_key == idempotency_key and duplicate.input_hash != input_hash:
                    raise CustomError(
                        ErrorCodes.CONFLICT,
                        "Idempotency key was already used for a different request",
                        status.HTTP_409_CONFLICT,
                    )
                await cache.complete_idempotency(
                    "parse", data.company_id, idempotency_key, str(duplicate.id), owner_token
                )
                return duplicate
            raise
        except Exception:
            await cache.release_idempotency("parse", data.company_id, idempotency_key, owner_token)
            raise
