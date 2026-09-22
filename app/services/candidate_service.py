import hashlib

from fastapi import status
from pymongo.errors import DuplicateKeyError

from app.core import cache
from app.core.config import settings
from app.core.errors import CustomError, ErrorCodes
from app.core.security import CurrentUser
from app.models.candidate import Candidate
from app.repositories.candidate_repository import CandidateRepository
from app.schemas.candidate import CandidateCreate, CandidateUpdate
from app.services.tenant_access_service import TenantAccessService


class CandidateService:
    @staticmethod
    def _cache_key(company_id: str, candidate_id: str) -> str:
        return cache.cache_key("candidate", company_id, candidate_id)

    @staticmethod
    def _list_cache_key(company_id: str, page: int, size: int, search: str | None) -> str:
        search_hash = hashlib.sha256((search or "").strip().lower().encode("utf-8")).hexdigest()[:16]
        return cache.cache_key("candidate-list", company_id, page, size, search_hash)

    @staticmethod
    async def create(data: CandidateCreate, current_user: CurrentUser) -> Candidate:
        await TenantAccessService.require_company_access(current_user, data.company_id)
        if await CandidateRepository.find_duplicate(data):
            raise CustomError(
                ErrorCodes.CONFLICT,
                "A candidate with the same email or phone already exists in this company",
                status.HTTP_409_CONFLICT,
            )
        try:
            candidate = await CandidateRepository.create(data, current_user.user_id)
        except DuplicateKeyError as exc:
            raise CustomError(
                ErrorCodes.CONFLICT,
                "Candidate already exists",
                status.HTTP_409_CONFLICT,
            ) from exc
        await cache.set_json(
            CandidateService._cache_key(data.company_id, str(candidate.id)),
            candidate.model_dump(by_alias=True),
            settings.CANDIDATE_CACHE_TTL,
        )
        await cache.delete_pattern(cache.cache_key("candidate-list", data.company_id, "*"))
        return candidate

    @staticmethod
    async def get(candidate_id: str, company_id: str, current_user: CurrentUser) -> Candidate:
        await TenantAccessService.require_company_access(current_user, company_id)
        key = CandidateService._cache_key(company_id, candidate_id)
        cached = await cache.get_json(key)
        if cached:
            try:
                return Candidate.model_validate(cached)
            except Exception:
                await cache.delete_keys(key)

        candidate = await CandidateRepository.get(candidate_id, company_id)
        if not candidate:
            raise CustomError(ErrorCodes.NOT_FOUND, "Candidate not found", status.HTTP_404_NOT_FOUND)
        await cache.set_json(key, candidate.model_dump(by_alias=True), settings.CANDIDATE_CACHE_TTL)
        return candidate

    @staticmethod
    async def list(
        company_id: str,
        page: int,
        size: int,
        search: str | None,
        current_user: CurrentUser,
    ) -> tuple[list[Candidate], int]:
        await TenantAccessService.require_company_access(current_user, company_id)
        size = min(size, settings.MAX_PAGE_SIZE)
        key = CandidateService._list_cache_key(company_id, page, size, search)
        cached = await cache.get_json(key)
        if cached is not None:
            try:
                return [Candidate.model_validate(item) for item in cached["items"]], int(cached["total"])
            except (KeyError, TypeError, ValueError):
                await cache.delete_keys(key)
        items, total = await CandidateRepository.list(company_id, page, size, search)
        await cache.set_json(
            key,
            {"items": [item.model_dump(by_alias=True) for item in items], "total": total},
            settings.CANDIDATE_CACHE_TTL,
        )
        return items, total

    @staticmethod
    async def update(
        candidate_id: str,
        company_id: str,
        data: CandidateUpdate,
        current_user: CurrentUser,
    ) -> Candidate:
        await TenantAccessService.require_company_access(current_user, company_id)
        candidate = await CandidateRepository.get(candidate_id, company_id)
        if not candidate:
            raise CustomError(ErrorCodes.NOT_FOUND, "Candidate not found", status.HTTP_404_NOT_FOUND)
        try:
            candidate = await CandidateRepository.update(candidate, data, current_user.user_id)
        except DuplicateKeyError as exc:
            raise CustomError(
                ErrorCodes.CONFLICT,
                "Candidate email or phone conflicts with another record",
                status.HTTP_409_CONFLICT,
            ) from exc
        await cache.delete_keys(CandidateService._cache_key(company_id, candidate_id))
        await cache.delete_pattern(cache.cache_key("candidate-list", company_id, "*"))
        return candidate
