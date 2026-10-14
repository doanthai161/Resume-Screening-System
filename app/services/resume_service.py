from app.core.transactions import current_session, transactional
import asyncio
import hashlib
import os
import stat
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
from app.core.tenant_policy import active_membership_filter, branch_role
from app.models.ai_model import AIModel
from app.models.candidate import Candidate
from app.models.company_branch import CompanyBranch
from app.models.company import Company
from app.models.user import User
from app.models.user_company import UserCompany
from app.models.resume_file import ResumeFile
from app.models.screening_run import ResumeParseRun
from app.schemas.resume import ParseResumeRequest, ResumeListResponse, ResumeResponse
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
    async def _owned_candidate(resume: ResumeFile, current_user: CurrentUser) -> bool:
        if not current_user.is_candidate or not resume.candidate_id:
            return False
        return bool(await Candidate.find_one({
            "_id": resume.candidate_id, "company_id": resume.company_id,
            "user_id": ObjectId(current_user.user_id), "is_deleted": False,
        }, session=current_session()))

    @staticmethod
    async def _authorize_record(resume: ResumeFile, current_user: CurrentUser, *, allow_own: bool = False) -> None:
        user = await User.get(ObjectId(current_user.user_id), session=current_session())
        if not user or not user.is_active:
            raise CustomError(ErrorCodes.FORBIDDEN, "Active user required", 403)
        company = await Company.get(resume.company_id, session=current_session())
        if not company or not company.is_active:
            raise CustomError(ErrorCodes.NOT_FOUND, "Company not found", 404)
        if resume.company_branch_id:
            branch = await CompanyBranch.find_one({
                "_id": resume.company_branch_id, "company_id": resume.company_id, "is_active": True,
            }, session=current_session())
            if not branch:
                raise CustomError(ErrorCodes.FORBIDDEN, "Resume branch access denied", 403)
        if allow_own and await ResumeService._owned_candidate(resume, current_user):
            return
        await TenantAccessService.require_company_access(current_user, str(resume.company_id))
        if resume.company_branch_id:
            if not await branch_role(current_user.user_id, str(resume.company_branch_id)):
                raise CustomError(ErrorCodes.FORBIDDEN, "Resume branch access denied", 403)
        elif not (user.is_superuser or company.user_id == user.id):
            raise CustomError(ErrorCodes.FORBIDDEN, "Branchless resumes require company ownership", 403)

    @staticmethod
    async def authorized_resume(resume_id: str, current_user: CurrentUser, *, permission: str = "resume_files:view") -> ResumeFile:
        if not current_user.has_permission(permission):
            raise CustomError(ErrorCodes.FORBIDDEN, "Resume permission denied", 403)
        if not ObjectId.is_valid(resume_id):
            raise CustomError(ErrorCodes.VALIDATION, "Invalid resume ID", 422)
        resume = await ResumeFile.find_one({"_id": ObjectId(resume_id), "is_deleted": False}, session=current_session())
        if not resume or not resume.company_id:
            raise CustomError(ErrorCodes.NOT_FOUND, "Resume not found", 404)
        await ResumeService._authorize_record(resume, current_user, allow_own=permission == "resume_files:view")
        return resume

    @staticmethod
    async def list_resumes(company_id: str, current_user: CurrentUser, *, page: int = 1, size: int = 20,
                           candidate_id: str | None = None) -> ResumeListResponse:
        if not current_user.has_permission("resume_files:view"):
            raise CustomError(ErrorCodes.FORBIDDEN, "Resume permission denied", 403)
        if (not ObjectId.is_valid(company_id) or (candidate_id and not ObjectId.is_valid(candidate_id))
                or page < 1 or not 1 <= size <= 100):
            raise CustomError(ErrorCodes.VALIDATION, "Invalid resume filter or pagination", 422)
        user = await User.get(ObjectId(current_user.user_id), session=current_session())
        company = await Company.get(ObjectId(company_id), session=current_session())
        if not user or not user.is_active:
            raise CustomError(ErrorCodes.FORBIDDEN, "Active user required", 403)
        if not company or not company.is_active:
            raise CustomError(ErrorCodes.NOT_FOUND, "Company not found", 404)
        branches = await CompanyBranch.get_motor_collection().find(
            {"company_id": company.id, "is_active": True}, {"_id": 1}, session=current_session()
        ).to_list(length=None)
        active_ids = [item["_id"] for item in branches]
        owner = user.is_superuser or company.user_id == user.id
        owned_ids = []
        if current_user.is_candidate:
            owned = await Candidate.get_motor_collection().find({
                "company_id": company.id, "user_id": user.id, "is_deleted": False,
            }, {"_id": 1}, session=current_session()).to_list(length=None)
            owned_ids = [item["_id"] for item in owned]
        if owner:
            scope = {"company_branch_id": {"$in": active_ids + [None]}}
        else:
            memberships = await UserCompany.get_motor_collection().find({
                "user_id": user.id, "company_branch_id": {"$in": active_ids}, **active_membership_filter(),
            }, {"company_branch_id": 1}, session=current_session()).to_list(length=None)
            allowed = [item["company_branch_id"] for item in memberships]
            if not allowed and not owned_ids:
                raise CustomError(ErrorCodes.FORBIDDEN, "Company access denied", 403)
            clauses = [{"company_branch_id": {"$in": allowed}}]
            if owned_ids:
                clauses.append({"candidate_id": {"$in": owned_ids},
                                "company_branch_id": {"$in": active_ids + [None]}})
            scope = {"$or": clauses}
        query = {"company_id": company.id, "is_deleted": False, **scope}
        if candidate_id:
            query["candidate_id"] = ObjectId(candidate_id)
        projection = {"_id": 1, **{field: 1 for field in ResumeResponse.model_fields if field != "id"}}
        collection = ResumeFile.get_motor_collection()
        total = await collection.count_documents(query, session=current_session())
        items = await collection.find(query, projection, session=current_session()).sort(
            [("uploaded_at", -1), ("_id", -1)]
        ).skip((page - 1) * size).limit(size).to_list(length=size)
        return ResumeListResponse(items=[ResumeResponse.model_validate(item) for item in items],
                                  total=total, page=page, size=size)

    @staticmethod
    def _open_download(resume: ResumeFile):
        if resume.storage_provider != "local":
            raise CustomError(ErrorCodes.NOT_FOUND, "Resume download is unavailable", 404)
        try:
            root = settings.resume_upload_path.absolute()
            target = Path(resume.file_path).absolute()
            relative = target.relative_to(root)
            if any(part in {"", ".", ".."} for part in relative.parts):
                raise ValueError()
            # Flat files remain readable for legacy uploads. New company folders
            # must match this document's tenant exactly.
            if len(relative.parts) not in {1, 2} or (len(relative.parts) == 2 and relative.parts[0] != str(resume.company_id)):
                raise ValueError()
            walk = root
            for part in (None, *relative.parts):
                if part is not None:
                    walk /= part
                info = walk.lstat()
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
                    raise ValueError()
            if not target.resolve(strict=True).is_relative_to(root.resolve(strict=True)):
                raise ValueError()
            before = target.lstat()
            if not stat.S_ISREG(before.st_mode) or before.st_size != resume.file_size or before.st_size > settings.MAX_RESUME_SIZE:
                raise ValueError()
            fd = os.open(target, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
            handle = os.fdopen(fd, "rb")
            try:
                opened = os.fstat(handle.fileno())
                if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino) or opened.st_size != resume.file_size:
                    raise ValueError()
                digest = hashlib.sha256()
                while chunk := handle.read(64 * 1024):
                    digest.update(chunk)
                if digest.hexdigest() != resume.checksum:
                    raise ValueError()
                handle.seek(0)
                return handle
            except BaseException:
                handle.close()
                raise
        except (OSError, ValueError):
            raise CustomError(ErrorCodes.NOT_FOUND, "Resume file is unavailable", 404) from None

    @staticmethod
    async def get_parse_run(
        resume_id: str,
        run_id: str,
        current_user: CurrentUser,
    ) -> ResumeParseRun:
        if not ObjectId.is_valid(resume_id) or not ObjectId.is_valid(run_id):
            raise CustomError(
                ErrorCodes.VALIDATION,
                "Invalid resume or parse run ID",
                status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        resume = await ResumeService.authorized_resume(resume_id, current_user)
        run = await ResumeParseRun.find_one(
            {
                "_id": PydanticObjectId(run_id),
                "resume_file_id": PydanticObjectId(resume_id),
                "company_id": resume.company_id,
            }
        )
        if not run:
            raise CustomError(
                ErrorCodes.NOT_FOUND,
                "Resume parse run not found",
                status.HTTP_404_NOT_FOUND,
            )
        return run

    @staticmethod
    async def upload(
        company_id: str,
        candidate_id: str,
        company_branch_id: Optional[str],
        file: UploadFile,
        current_user: CurrentUser,
    ) -> ResumeFile:
        if not ObjectId.is_valid(company_id):
            raise CustomError(
                ErrorCodes.VALIDATION,
                "Invalid company ID",
                status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        if not ObjectId.is_valid(candidate_id):
            raise CustomError(
                ErrorCodes.VALIDATION,
                "Invalid candidate ID",
                status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        if company_branch_id and not ObjectId.is_valid(company_branch_id):
            raise CustomError(
                ErrorCodes.VALIDATION,
                "Invalid company branch ID",
                status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        candidate = await Candidate.find_one(
            {
                "_id": PydanticObjectId(candidate_id),
                "company_id": PydanticObjectId(company_id),
                "is_deleted": False,
            }
        )
        if not candidate:
            raise CustomError(
                ErrorCodes.NOT_FOUND, "Candidate not found", status.HTTP_404_NOT_FOUND
            )
        company = await Company.get(PydanticObjectId(company_id))
        if not company or not company.is_active:
            raise CustomError(
                ErrorCodes.NOT_FOUND, "Company not found", status.HTTP_404_NOT_FOUND
            )

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
                raise CustomError(
                    ErrorCodes.NOT_FOUND,
                    "Company branch not found",
                    status.HTTP_404_NOT_FOUND,
                )
            branch_id = branch.id
            if not is_own_candidate and not await branch_role(current_user.user_id, company_branch_id):
                raise CustomError(ErrorCodes.FORBIDDEN, "Resume branch access denied", 403)
        elif not is_own_candidate and not (current_user.is_superuser or str(company.user_id) == current_user.user_id):
            raise CustomError(ErrorCodes.FORBIDDEN, "A permitted branch is required for resume upload", 403)

        original_name = Path((file.filename or "resume").replace("\\", "/")).name
        if len(original_name) > 255:
            raise CustomError(
                ErrorCodes.BAD_REQUEST,
                "Resume filename is too long",
                status.HTTP_400_BAD_REQUEST,
            )
        extension = Path(original_name).suffix.lower().lstrip(".")
        if extension not in settings.allowed_resume_extensions_list:
            raise CustomError(
                ErrorCodes.BAD_REQUEST,
                "Unsupported resume extension",
                status.HTTP_400_BAD_REQUEST,
            )
        if file.content_type not in settings.allowed_resume_mime_types:
            raise CustomError(
                ErrorCodes.BAD_REQUEST,
                "Unsupported resume MIME type",
                status.HTTP_400_BAD_REQUEST,
            )

        settings.temp_upload_path.mkdir(parents=True, exist_ok=True)
        settings.resume_upload_path.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        temp_path = settings.temp_upload_path / f"{token}.upload"
        final_name = f"{token}.{extension}"
        company_dir = settings.resume_upload_path / str(company.id)
        company_dir.mkdir(parents=True, exist_ok=True)
        if company_dir.is_symlink() or company_dir.resolve().parent != settings.resume_upload_path.resolve():
            raise CustomError(ErrorCodes.INTERNAL, "Resume storage is unavailable", 503)
        final_path = company_dir / final_name
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
                raise CustomError(
                    ErrorCodes.BAD_REQUEST,
                    "Resume file is empty",
                    status.HTTP_400_BAD_REQUEST,
                )
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
                if existing.company_branch_id != branch_id:
                    raise CustomError(ErrorCodes.CONFLICT, "Resume already exists in another branch", 409)
                await ResumeService._authorize_record(existing, current_user, allow_own=is_own_candidate)
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
                object_key=f"{company.id}/{final_name}",
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
                    if duplicate.company_branch_id != branch_id:
                        raise CustomError(ErrorCodes.CONFLICT, "Resume already exists in another branch", 409)
                    await ResumeService._authorize_record(duplicate, current_user, allow_own=is_own_candidate)
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
    @transactional
    async def start_parse(
        resume_id: str,
        data: ParseResumeRequest,
        idempotency_key: str,
        current_user: CurrentUser,
    ) -> ResumeParseRun:
        resume = await ResumeService.authorized_resume(resume_id, current_user, permission="resume_files:parse")
        if str(resume.company_id) != data.company_id:
            raise CustomError(ErrorCodes.FORBIDDEN, "Resume does not belong to this company", 403)

        requested_model_id = PydanticObjectId(data.parser_model_id) if data.parser_model_id else None
        existing = await ResumeParseRun.find_one({
            "company_id": resume.company_id, "idempotency_key": idempotency_key,
        }, session=current_session())
        if existing:
            if (existing.resume_file_id != resume.id or existing.parser_version != data.parser_version
                    or existing.parser_model_id != requested_model_id):
                raise CustomError(ErrorCodes.CONFLICT, "Idempotency key was already used for a different request", 409)
            # Replay the durable command even if its original parser is now inactive.
            return existing

        parser_model_id = None
        if data.parser_model_id:
            model = await AIModel.find_one(
                {
                    "_id": PydanticObjectId(data.parser_model_id),
                    "model_type": "resume_parser",
                    "is_active": True,
                },
                session=current_session(),
            )
            if not model:
                raise CustomError(
                    ErrorCodes.NOT_FOUND,
                    "Parser model not found",
                    status.HTTP_404_NOT_FOUND,
                )
            parser_model_id = model.id

        input_hash = hashlib.sha256(
            f"{resume.id}:{resume.checksum}:{data.parser_version}:{parser_model_id or 'default'}".encode(
                "utf-8"
            )
        ).hexdigest()
        if await ResumeParseRun.find_one({"resume_file_id": resume.id, "is_terminal": False}, session=current_session()):
            raise CustomError(ErrorCodes.CONFLICT, "Another parse is active; retry with its original idempotency key", 409)
        # Serialize parsing with source-sensitive HR/screening writes. No file
        # work occurs inside this transaction; only the resume document is fenced.
        fenced = await ResumeFile.find_one({
            "_id": resume.id, "company_id": resume.company_id, "is_deleted": False,
            "checksum": resume.checksum, "company_branch_id": resume.company_branch_id,
        }, session=current_session()).update({"$inc": {"screening_fence_version": 1}}, session=current_session())
        if not fenced or fenced.matched_count != 1:
            raise CustomError(ErrorCodes.CONFLICT, "Resume changed concurrently", 409)

        owner_token = uuid.uuid4().hex
        if not await cache.reserve_idempotency(
            "parse", data.company_id, idempotency_key, owner_token
        ):
            raise CustomError(
                ErrorCodes.CONFLICT,
                "Parse request is already processing",
                status.HTTP_409_CONFLICT,
            )
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
            await run.insert(session=current_session())
            message_id = await job_queue.enqueue(
                "resume-parse", str(run.id), data.company_id
            )
            if message_id:
                await ResumeParseRun.find_one(
                    {"_id": run.id, "status": "queued"}, session=current_session()
                ).update(
                    {
                        "$set": {
                            "queue_message_id": str(message_id),
                            "last_enqueued_at": now_utc(),
                            "updated_at": now_utc(),
                        }
                    },
                    session=current_session(),
                )
            await cache.complete_idempotency(
                "parse", data.company_id, idempotency_key, str(run.id), owner_token
            )
            return run
        except Exception:
            # Transaction abort removes the run; unique/CAS conflicts are mapped
            # by the transaction boundary. Retry with the same idempotency key.
            raise
