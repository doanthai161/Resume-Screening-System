import re
from typing import Optional, Tuple

from beanie import PydanticObjectId
from pymongo.errors import DuplicateKeyError

from app.models.candidate import Candidate
from app.schemas.candidate import CandidateCreate, CandidateUpdate
from app.utils.time import now_utc


class CandidateRepository:
    @staticmethod
    async def create(data: CandidateCreate, created_by: str) -> Candidate:
        candidate = Candidate(
            company_id=PydanticObjectId(data.company_id),
            user_id=PydanticObjectId(data.user_id) if data.user_id else None,
            full_name=data.full_name.strip(),
            email=data.email,
            phone_number=data.phone_number,
            location=data.location,
            source=data.source,
            tags=data.tags,
            metadata=data.metadata,
            created_by=PydanticObjectId(created_by),
        )
        await candidate.insert()
        return candidate

    @staticmethod
    async def get(candidate_id: str, company_id: str) -> Optional[Candidate]:
        if not PydanticObjectId.is_valid(candidate_id):
            return None
        return await Candidate.find_one(
            {
                "_id": PydanticObjectId(candidate_id),
                "company_id": PydanticObjectId(company_id),
                "is_deleted": False,
            }
        )

    @staticmethod
    async def find_duplicate(data: CandidateCreate) -> Optional[Candidate]:
        conditions = []
        if data.email:
            conditions.append({"normalized_email": data.email.strip().lower()})
        if data.phone_number:
            prefix = "+" if data.phone_number.startswith("+") else ""
            digits = "".join(ch for ch in data.phone_number if ch.isdigit())
            conditions.append({"normalized_phone": f"{prefix}{digits}"})
        if not conditions:
            return None
        return await Candidate.find_one(
            {
                "company_id": PydanticObjectId(data.company_id),
                "is_deleted": False,
                "$or": conditions,
            }
        )

    @staticmethod
    async def list(
        company_id: str,
        page: int,
        size: int,
        search: Optional[str] = None,
    ) -> Tuple[list[Candidate], int]:
        query: dict = {"company_id": PydanticObjectId(company_id), "is_deleted": False}
        if search:
            escaped = re.escape(search.strip()[:100])
            query["$or"] = [
                {"full_name": {"$regex": escaped, "$options": "i"}},
                {"normalized_email": {"$regex": escaped.lower(), "$options": "i"}},
                {"normalized_phone": {"$regex": escaped}},
            ]
        total = await Candidate.find(query).count()
        items = await Candidate.find(query).sort("-created_at").skip((page - 1) * size).limit(size).to_list()
        return items, total

    @staticmethod
    async def update(candidate: Candidate, data: CandidateUpdate, updated_by: str) -> Candidate:
        updates = data.model_dump(exclude_unset=True)
        for key, value in updates.items():
            setattr(candidate, key, value)
        if "email" in updates:
            candidate.email = candidate.email.strip().lower() if candidate.email else None
            candidate.normalized_email = candidate.email
        if "phone_number" in updates:
            if candidate.phone_number:
                candidate.phone_number = candidate.phone_number.strip()
                prefix = "+" if candidate.phone_number.startswith("+") else ""
                digits = "".join(ch for ch in candidate.phone_number if ch.isdigit())
                candidate.normalized_phone = f"{prefix}{digits}" or None
            else:
                candidate.normalized_phone = None
        candidate.updated_by = PydanticObjectId(updated_by)
        candidate.updated_at = now_utc()
        await candidate.save()
        return candidate


__all__ = ["CandidateRepository", "DuplicateKeyError"]
