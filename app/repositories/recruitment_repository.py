from typing import Optional, Tuple

from beanie import PydanticObjectId

from app.models.application_review import ApplicationReview
from app.models.application_stage_event import ApplicationStageEvent
from app.models.job_application import JobApplication
from app.models.job_scorecard import JobScorecard
from app.models.screening_run import ScreeningRun


class RecruitmentRepository:
    @staticmethod
    async def get_application(application_id: str, company_id: str) -> Optional[JobApplication]:
        if not PydanticObjectId.is_valid(application_id) or not PydanticObjectId.is_valid(company_id):
            return None
        return await JobApplication.find_one(
            {
                "_id": PydanticObjectId(application_id),
                "company_id": PydanticObjectId(company_id),
            }
        )

    @staticmethod
    async def list_applications(
        company_id: str,
        page: int,
        size: int,
        job_requirement_id: Optional[str] = None,
        stage: Optional[str] = None,
        applicant_id: Optional[str] = None,
    ) -> Tuple[list[JobApplication], int]:
        query: dict = {"company_id": PydanticObjectId(company_id)}
        if job_requirement_id:
            query["job_requirement_id"] = PydanticObjectId(job_requirement_id)
        if stage:
            query["current_stage"] = stage
        if applicant_id:
            query["applicant_id"] = PydanticObjectId(applicant_id)
        total = await JobApplication.find(query).count()
        items = await JobApplication.find(query).sort("-created_at").skip((page - 1) * size).limit(size).to_list()
        return items, total

    @staticmethod
    async def get_scorecard(scorecard_id: str, company_id: str) -> Optional[JobScorecard]:
        if not PydanticObjectId.is_valid(scorecard_id) or not PydanticObjectId.is_valid(company_id):
            return None
        return await JobScorecard.find_one(
            {"_id": PydanticObjectId(scorecard_id), "company_id": PydanticObjectId(company_id)}
        )

    @staticmethod
    async def get_active_scorecard(job_requirement_id: str, company_id: str) -> Optional[JobScorecard]:
        if not PydanticObjectId.is_valid(job_requirement_id) or not PydanticObjectId.is_valid(company_id):
            return None
        return await JobScorecard.find_one(
            {
                "job_requirement_id": PydanticObjectId(job_requirement_id),
                "company_id": PydanticObjectId(company_id),
                "is_active": True,
            }
        )

    @staticmethod
    async def get_screening_run(run_id: str, company_id: str) -> Optional[ScreeningRun]:
        if not PydanticObjectId.is_valid(run_id) or not PydanticObjectId.is_valid(company_id):
            return None
        return await ScreeningRun.find_one(
            {"_id": PydanticObjectId(run_id), "company_id": PydanticObjectId(company_id)}
        )

    @staticmethod
    async def list_reviews(application_id: str, company_id: str) -> list[ApplicationReview]:
        if not PydanticObjectId.is_valid(application_id) or not PydanticObjectId.is_valid(company_id):
            return []
        return await ApplicationReview.find(
            {
                "application_id": PydanticObjectId(application_id),
                "company_id": PydanticObjectId(company_id),
            }
        ).sort("-created_at").to_list()


__all__ = [
    "RecruitmentRepository",
    "ApplicationStageEvent",
    "JobApplication",
    "JobScorecard",
    "ScreeningRun",
    "ApplicationReview",
]
