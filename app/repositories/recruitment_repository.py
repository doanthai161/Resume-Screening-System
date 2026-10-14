from app.core.transactions import current_session
from typing import Optional, Tuple

from beanie import PydanticObjectId

from app.models.application_review import ApplicationReview
from app.models.application_stage_event import ApplicationStageEvent
from app.models.job_application import JobApplication
from app.models.job_requirement import JobRequirement
from app.models.job_scorecard import JobScorecard
from app.models.screening_run import ScreeningRun
from app.models.resume_file import ResumeFile


class RecruitmentRepository:
    @staticmethod
    async def get_application(
        application_id: str, company_id: str
    ) -> Optional[JobApplication]:
        if not PydanticObjectId.is_valid(
            application_id
        ) or not PydanticObjectId.is_valid(company_id):
            return None
        return await JobApplication.find_one(
            {
                "_id": PydanticObjectId(application_id),
                "company_id": PydanticObjectId(company_id),
            },
            session=current_session(),
        )

    @staticmethod
    async def list_applications(
        company_id: str,
        page: int,
        size: int,
        job_requirement_id: Optional[str] = None,
        stage: Optional[str] = None,
        applicant_id: Optional[str] = None,
        branch_ids: Optional[list[PydanticObjectId]] = None,
        scope_resume: bool = False,
        allow_branchless: bool = False,
    ) -> Tuple[list[JobApplication], int]:
        query: dict = {"company_id": PydanticObjectId(company_id)}
        if job_requirement_id:
            query["job_requirement_id"] = PydanticObjectId(job_requirement_id)
        if stage:
            query["current_stage"] = stage
        if applicant_id:
            query["applicant_id"] = PydanticObjectId(applicant_id)
        if branch_ids is not None:
            if scope_resume:
                # Legacy applications may lack their branch snapshot. The job
                # join below supplies the current scope before pagination.
                query["$or"] = [{"company_branch_id": {"$in": branch_ids}}, {"company_branch_id": None}]
            else:
                query["company_branch_id"] = {"$in": branch_ids}
        if scope_resume:
            cv_scope = [{"company_branch_id": {"$in": branch_ids or []}}]
            if allow_branchless:
                cv_scope.append({"company_branch_id": None})
            pipeline = [
                {"$match": query},
                {"$lookup": {"from": JobRequirement.get_settings().name, "localField": "job_requirement_id",
                             "foreignField": "_id", "as": "_job_scope"}},
                {"$unwind": "$_job_scope"},
                {"$match": {"_job_scope.company_branch_id": {"$in": branch_ids or []}, "$expr": {"$or": [
                    {"$eq": [{"$ifNull": ["$company_branch_id", None]}, None]},
                    {"$eq": ["$company_branch_id", "$_job_scope.company_branch_id"]},
                ]}}},
                {"$lookup": {"from": ResumeFile.get_settings().name, "localField": "resume_file_id",
                             "foreignField": "_id", "as": "_cv_scope"}},
                {"$match": {"_cv_scope": {"$elemMatch": {"company_id": PydanticObjectId(company_id),
                                                         "is_deleted": False, "$or": cv_scope}}}},
                {"$unset": ["_cv_scope", "_job_scope"]},
                {"$facet": {"items": [{"$sort": {"created_at": -1, "_id": -1}},
                                        {"$skip": (page - 1) * size}, {"$limit": size}],
                            "total": [{"$count": "count"}]}},
            ]
            rows = await JobApplication.get_motor_collection().aggregate(pipeline, session=current_session()).to_list(1)
            result = rows[0] if rows else {"items": [], "total": []}
            return [JobApplication.model_validate(item) for item in result["items"]], result["total"][0]["count"] if result["total"] else 0
        total = await JobApplication.find(query, session=current_session()).count()
        items = (
            await JobApplication.find(query, session=current_session())
            .sort([("created_at", -1), ("_id", -1)])
            .skip((page - 1) * size)
            .limit(size)
            .to_list()
        )
        return items, total

    @staticmethod
    async def get_scorecard(
        scorecard_id: str, company_id: str
    ) -> Optional[JobScorecard]:
        if not PydanticObjectId.is_valid(scorecard_id) or not PydanticObjectId.is_valid(
            company_id
        ):
            return None
        return await JobScorecard.find_one(
            {
                "_id": PydanticObjectId(scorecard_id),
                "company_id": PydanticObjectId(company_id),
            },
            session=current_session(),
        )

    @staticmethod
    async def get_active_scorecard(
        job_requirement_id: str, company_id: str
    ) -> Optional[JobScorecard]:
        if not PydanticObjectId.is_valid(
            job_requirement_id
        ) or not PydanticObjectId.is_valid(company_id):
            return None
        return await JobScorecard.find_one(
            {
                "job_requirement_id": PydanticObjectId(job_requirement_id),
                "company_id": PydanticObjectId(company_id),
                "is_active": True,
            },
            session=current_session(),
        )

    @staticmethod
    async def get_screening_run(run_id: str, company_id: str) -> Optional[ScreeningRun]:
        if not PydanticObjectId.is_valid(run_id) or not PydanticObjectId.is_valid(
            company_id
        ):
            return None
        return await ScreeningRun.find_one(
            {
                "_id": PydanticObjectId(run_id),
                "company_id": PydanticObjectId(company_id),
            },
            session=current_session(),
        )

    @staticmethod
    async def list_reviews(
        application_id: str, company_id: str, page: int = 1, size: int = 20
    ) -> list[ApplicationReview]:
        if not PydanticObjectId.is_valid(
            application_id
        ) or not PydanticObjectId.is_valid(company_id):
            return []
        return (
            await ApplicationReview.find(
                {
                    "application_id": PydanticObjectId(application_id),
                    "company_id": PydanticObjectId(company_id),
                },
                session=current_session(),
            )
            .sort([("created_at", -1), ("_id", -1)])
            .skip((page - 1) * size)
            .limit(size)
            .to_list()
        )


__all__ = [
    "RecruitmentRepository",
    "ApplicationStageEvent",
    "JobApplication",
    "JobScorecard",
    "ScreeningRun",
    "ApplicationReview",
]
