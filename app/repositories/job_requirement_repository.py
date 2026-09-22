from typing import List, Optional, Tuple, Dict, Any
import re
from bson import ObjectId
from beanie import PydanticObjectId

from app.models.job_requirement import JobRequirement, JobStatus
from app.schemas.job_requirement import JobRequirementCreate, JobRequirementUpdate
from app.utils.time import now_utc

class JobRequirementRepository:

    @staticmethod
    async def create_job_requirement(
        job_data: JobRequirementCreate
    ) -> JobRequirement:
        job_dict = job_data.model_dump()
        job_dict["user_id"] = ObjectId(job_dict["user_id"])
        job_dict["company_branch_id"] = ObjectId(job_dict["company_branch_id"])

        job = JobRequirement(**job_dict)
        await job.insert()
        return job

    @staticmethod
    async def get_job_requirement(
        job_id: str
    ) -> Optional[JobRequirement]:
        try:
            return await JobRequirement.get(PydanticObjectId(job_id))
        except Exception:
            return None

    @staticmethod
    async def update_job_requirement(
        job_id: str,
        update_data: JobRequirementUpdate
    ) -> Optional[JobRequirement]:
        if not PydanticObjectId.is_valid(job_id):
            return None
        job = await JobRequirement.get(PydanticObjectId(job_id))
        if not job:
            return None

        update_dict = update_data.model_dump(exclude_unset=True)
        for field, value in update_dict.items():
            setattr(job, field, value)

        if update_dict.get("status") is not None:
            target_status = JobStatus(update_dict["status"])
            job.status = target_status
            job.is_open = target_status == JobStatus.PUBLISHED
            job.is_active = target_status != JobStatus.ARCHIVED
            if target_status == JobStatus.PUBLISHED and job.published_at is None:
                job.published_at = now_utc()
            if target_status == JobStatus.CLOSED:
                job.closed_at = now_utc()
        elif "is_open" in update_dict:
            job.status = JobStatus.PUBLISHED if update_dict["is_open"] else JobStatus.CLOSED
            if job.status == JobStatus.PUBLISHED and job.published_at is None:
                job.published_at = now_utc()
            if job.status == JobStatus.CLOSED:
                job.closed_at = now_utc()
        if update_dict.get("is_active") is False:
            job.status = JobStatus.ARCHIVED
            job.is_open = False

        if job.salary_min is not None and job.salary_max is not None and job.salary_min > job.salary_max:
            raise ValueError("salary_min must be less than or equal to salary_max")
        if (job.salary_min is not None or job.salary_max is not None) and not job.salary_currency:
            raise ValueError("salary_currency is required when salary is provided")
        if job.salary_currency:
            job.salary_currency = job.salary_currency.upper()

        job.version += 1
        job.updated_at = now_utc()
        await job.save()
        return job

    @staticmethod
    async def delete_job_requirement(
        job_id: str
    ) -> bool:
        job = await JobRequirement.get(PydanticObjectId(job_id))
        if not job:
            return False

        job.is_active = False
        job.is_open = False
        job.status = JobStatus.ARCHIVED
        job.updated_at = now_utc()
        await job.save()
        return True

    @staticmethod
    async def list_job_requirements(
        user_id: Optional[str] = None,
        company_branch_id: Optional[str] = None,
        is_open: Optional[bool] = None,
        is_active: Optional[bool] = True,
        skip: int = 0,
        limit: int = 20,
        sort_by: str = "created_at",
        sort_order: int = -1
    ) -> Tuple[List[JobRequirement], int]:
        query_filter: Dict[str, Any] = {}

        if user_id:
            query_filter["user_id"] = ObjectId(user_id)
        if company_branch_id:
            query_filter["company_branch_id"] = ObjectId(company_branch_id)
        if is_open is not None:
            query_filter["is_open"] = is_open
        if is_active is not None:
            query_filter["is_active"] = is_active

        sort_expression = [(sort_by, sort_order)]

        jobs = await JobRequirement.find(
            query_filter,
            sort=sort_expression,
            skip=skip,
            limit=limit
        ).to_list()

        total = await JobRequirement.count(query_filter)

        return jobs, total

    @staticmethod
    async def search_job_requirements(
        search_term: Optional[str] = None,
        programming_languages: Optional[List[str]] = None,
        skills: Optional[List[str]] = None,
        experience_level: Optional[str] = None,
        skip: int = 0,
        limit: int = 20
    ) -> Tuple[List[JobRequirement], int]:
        query_filter: Dict[str, Any] = {
            "is_active": True,
            "is_open": True
        }

        if search_term:
            escaped_term = re.escape(search_term.strip()[:100])
            query_filter["$or"] = [
                {"title": {"$regex": escaped_term, "$options": "i"}},
                {"description": {"$regex": escaped_term, "$options": "i"}}
            ]

        if programming_languages:
            query_filter["programming_languages"] = {"$all": programming_languages}

        if skills:
            query_filter["skills_required"] = {"$all": skills}

        if experience_level:
            query_filter["experience_level"] = experience_level

        jobs = await JobRequirement.find(
            query_filter,
            sort=[("created_at", -1)],
            skip=skip,
            limit=limit
        ).to_list()

        total = await JobRequirement.count(query_filter)

        return jobs, total

    @staticmethod
    async def get_active_job_count(
        user_id: Optional[str] = None
    ) -> int:
        query_filter: Dict[str, Any] = {
            "is_active": True,
            "is_open": True
        }
        if user_id:
            query_filter["user_id"] = ObjectId(user_id)

        return await JobRequirement.count(query_filter)

    @staticmethod
    async def find_expired_open_jobs() -> List[JobRequirement]:
        query_filter = {
            "expiration_time": {"$lt": now_utc()},
            "is_open": True,
            "is_active": True
        }
        return await JobRequirement.find(query_filter).to_list()
