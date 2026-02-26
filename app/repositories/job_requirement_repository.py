from typing import List, Optional, Tuple, Dict, Any
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient
from beanie import PydanticObjectId
from pymongo.errors import DuplicateKeyError

from app.models.job_requirement import JobRequirement
from app.schemas.job_requirement import JobRequirementCreate, JobRequirementUpdate
from app.utils.time import now_utc

class JobRequirementRepository:
    def __init__(self):
        pass

    async def create_job_requirement(
        self,
        job_data: JobRequirementCreate
    ) -> JobRequirement:
        job_dict = job_data.model_dump()
        job_dict["user_id"] = ObjectId(job_dict["user_id"])
        job_dict["company_branch_id"] = ObjectId(job_dict["company_branch_id"])
        
        job = JobRequirement(**job_dict)
        await job.insert()
        return job

    async def get_job_requirement(
        self, 
        job_id: str
    ) -> Optional[JobRequirement]:
        try:
            return await JobRequirement.get(PydanticObjectId(job_id))
        except Exception:
            return None

    async def search_job_requirements(
        self,
        search_term: Optional[str] = None,
        programming_languages: Optional[List[str]] = None,
        skills: Optional[List[str]] = None,
        experience_level: Optional[str] = None,
        skip: int = 0,
        limit: int = 20,
    ) -> Tuple[List[JobRequirement], int]:
        pipeline = []

        match_stage = {}
        
        if search_term:
            match_stage["$text"] = {"$search": search_term}
        
        if programming_languages:
            match_stage["programming_languages"] = {"$in": programming_languages}
        if skills:
            match_stage["skills_required"] = {"$in": skills}
        if experience_level:
            match_stage["experience_level"] = experience_level
            
        match_stage["is_active"] = True
        if match_stage:
            pipeline.append({"$match": match_stage})

        pipeline.append({
            "$facet": {
                "data": [
                    {"$skip": skip},
                    {"$limit": limit}
                ],
                "count": [
                    {"$count": "total"}
                ]
            }
        })

        result = await JobRequirement.aggregate(pipeline).to_list(length=1)
        
        if not result:
            return [], 0

        facet_result = result[0]
        jobs = [JobRequirement(**item) for item in facet_result.get("data", [])]
        total = facet_result.get("count", [{}])[0].get("total", 0)

        return jobs, total

    async def update_job_requirement(
        self, 
        job_id: str, 
        update_data: JobRequirementUpdate
    ) -> Optional[JobRequirement]:
        try:
            job = await JobRequirement.get(PydanticObjectId(job_id))
            if not job:
                return None

            update_dict = update_data.model_dump(exclude_unset=True)
            
            if "user_id" in update_dict:
                update_dict["user_id"] = ObjectId(update_dict["user_id"])
            if "company_branch_id" in update_dict:
                update_dict["company_branch_id"] = ObjectId(update_dict["company_branch_id"])

            for field, value in update_dict.items():
                setattr(job, field, value)
            
            job.updated_at = now_utc()
            await job.save()
            return job
        except Exception:
            return None

    async def delete_job_requirement(
        self, 
        job_id: str
    ) -> bool:
        job = await JobRequirement.get(PydanticObjectId(job_id))
        if not job:
            return False
        
        job.is_active = False
        job.updated_at = now_utc()
        await job.save()
        return True

    async def list_job_requirements(
        self,
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

    async def search_job_requirements(
        self,
        search_term: str,
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
            query_filter["$or"] = [
                {"title": {"$regex": search_term, "$options": "i"}},
                {"description": {"$regex": search_term, "$options": "i"}}
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

    async def get_active_job_count(
        self, 
        user_id: Optional[str] = None
    ) -> int:
        query_filter: Dict[str, Any] = {
            "is_active": True,
            "is_open": True
        }
        if user_id:
            query_filter["user_id"] = ObjectId(user_id)
        
        return await JobRequirement.count(query_filter)

    async def find_expired_open_jobs(self) -> List[JobRequirement]:
        from datetime import datetime
        
        query_filter = {
            "expiration_time": {"$lt": now_utc()},
            "is_open": True,
            "is_active": True
        }
        return await JobRequirement.find(query_filter).to_list()
