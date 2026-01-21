from fastapi import APIRouter, Depends, HTTPException, status, FastAPI, Request, BackgroundTasks
from bson import ObjectId
from app.utils.time import now_vn
from app.models.company_branch import CompanyBranch
from app.models.job_requirement import JobRequirement
from app.schemas.job_requirement import JobRequirementCreate, JobRequirementResponse, JobRequirementListResponse
from app.core.rate_limiter import limiter
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler
from app.logs.logging_config import logger
from app.core.security import (
    CurrentUser,
    require_permission,
)

router = APIRouter()
app = FastAPI()

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

@router.post("/create-job-requirement", response_model=JobRequirementResponse)
@limiter.limit("3/minute")
async def create_job_requirement(
    request: Request,
    data: JobRequirementCreate,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(require_permission("job_requirements:create")),
):
    background_tasks.add_task(
        logger.info,
        f"Creating job requirement with title: {data.title}"
    )
    company_branch = await CompanyBranch.find_one(
        {"_id": ObjectId(data.company_branch_id), "is_active": True}
    )
    if not company_branch:
        raise HTTPException(404, "Company branch not found")
    job_requirement = JobRequirement(
        user_id=ObjectId(current_user.user_id),
        company_branch_id=ObjectId(data.company_branch_id),
        title=data.title,
        programming_languages=data.programming_languages,
        skills_required=data.skills_required,
        experience_level=data.experience_level,
        description=data.description,
        salary_min=data.salary_min,
        salary_max=data.salary_max,
        expiration_time=data.expiration_time,
        is_open=data.is_open,
        is_active=True,
        created_at=now_vn(),
        updated_at=now_vn()
    )
    try: 
        await job_requirement.insert()
    except Exception as exc:
        if "E11000" in str(exc):
            raise HTTPException(409, "Job requirement already exists")
        raise
    return JobRequirementResponse(
        id=str(job_requirement.id),
        user_id=str(job_requirement.user_id),
        company_branch_id=str(job_requirement.company_branch_id),
        title=job_requirement.title,
        programming_languages=job_requirement.programming_languages,
        skills_required=job_requirement.skills_required,
        experience_level=job_requirement.experience_level,
        description=job_requirement.description,
        salary_min=job_requirement.salary_min,
        salary_max=job_requirement.salary_max,
        expiration_time=job_requirement.expiration_time,
        is_open=job_requirement.is_open,
        created_at=job_requirement.created_at,
        updated_at=job_requirement.updated_at
    )

@router.get("/list-job-requirements", response_model=JobRequirementListResponse)
@limiter.limit("10/minute")
async def list_job_requirements(
    request: Request,
    background_tasks: BackgroundTasks,
    page: int = 1,
    size: int = 10,
    current_user: CurrentUser = Depends(require_permission("job_requirements:view")),
):
    background_tasks.add_task(
        logger.info,
        f"User {current_user.user_id} listing job requirements: page={page}, size={size}"
    )
    if page < 1 or size < 1:
        raise HTTPException(400, "Page and size must be greater than 0")
    skip = (page - 1) * size
    job_requirements = await JobRequirement.find(JobRequirement.is_active == True).to_list()
    total = len(job_requirements)
    return JobRequirementListResponse(
        job_requirements=job_requirements,
        total=total,
        page=page,
        size=size
    )
    return JobRequirementListResponse(
        job_requirements=[
            JobRequirementResponse(
                id=str(jr.id),
                user_id=str(jr.user_id),
                company_branch_id=str(jr.company_branch_id),
                title=jr.title,
                programming_languages=jr.programming_languages,
                skills_required=jr.skills_required,
                experience_level=jr.experience_level,
                description=jr.description,
                salary_min=jr.salary_min,
                salary_max=jr.salary_max,
                expiration_time=jr.expiration_time,
                is_open=jr.is_open,
                created_at=jr.created_at,
                updated_at=jr.updated_at
            ) for jr in job_requirements
        ],
        total=total,
        page=page,
        size=size
    )