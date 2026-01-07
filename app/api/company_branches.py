from bson import ObjectId
from fastapi import Request, BackgroundTasks, APIRouter, HTTPException, status, FastAPI, Depends, Query
from app.utils.time import now_vn
from app.models.company_branch import CompanyBranch
from app.schemas.company_branch import CompanyBranchCreate, CompanyBranchResponse, CompanyBranchUpdate, CompanyBranchListResponse
from app.core.rate_limiter import limiter
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler
from app.logs.logging_config import logger
from app.core.security import (
    CurrentUser,
    require_permission,
    get_current_user,
)


router = APIRouter()
app = FastAPI()

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@router.post("/company-branches", response_model=CompanyBranchResponse, status_code=201)
@limiter.limit("3/minute")
async def create_company_branch(
    request: Request,
    data: CompanyBranchCreate,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(
        require_permission("company_branches:create")
    ),
):
    background_tasks.add_task(
        logger.info,
        f"User {current_user.user_id} creating company branch: {data.branch_name}"
    )

    company_branch = CompanyBranch(
        company_id=ObjectId(data.company_id),
        bussiness_type=data.bussiness_type,
        branch_name=data.branch_name,
        phone_number=data.phone_number,
        address=data.address,
        description=data.description,
        company_type=data.company_type,
        company_size=data.company_size,
        company_industry=data.company_industry,
        country=data.country,
        city=data.city,
        working_days=data.working_days,
        overtime_policy=data.overtime_policy,
        is_active=True,
        created_at=now_vn(),
        created_by=ObjectId(current_user.user_id),
        updated_by=None,
        updated_at=now_vn()
    )

    try:
        await company_branch.insert()
    except Exception as exc:
        if "E11000" in str(exc):
            raise HTTPException(
                status_code=409,
                detail="Company branch already exists"
            )
        raise

    return CompanyBranchResponse.model_validate(company_branch)


@router.get("/company-branches", response_model=CompanyBranchListResponse)
@limiter.limit("10/minute")
async def list_company_branches(
    request: Request,
    background_tasks: BackgroundTasks,
    company_id: str = Query(...),
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=100),
    current_user: CurrentUser = Depends(
        require_permission("company_branches:view")
    ),
):
    try:
        company_oid = ObjectId(company_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid company_id")

    skip = (page - 1) * size

    filter_query = {
        "company_id": company_oid,
        "is_active": True
    }

    background_tasks.add_task(
        logger.info,
        f"User {current_user.user_id} listing branches of company {company_id}, page={page}, size={size}"
    )

    total = await CompanyBranch.count_documents(filter_query)

    branches = await CompanyBranch.find(
        filter_query
    ).skip(skip).limit(size).to_list()

    return CompanyBranchListResponse(
        total=total,
        page=page,
        size=size,
        company_branches=[
            CompanyBranchResponse.model_validate(branch)
            for branch in branches
        ]
    )


@router.patch("/company-branches/{branch_id}", response_model=CompanyBranchResponse)
@limiter.limit("3/minute")
async def update_company_branch(
    request: Request,
    branch_id: str,
    data: CompanyBranchUpdate,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(
        require_permission("company_branches:edit")
    ),
):
    try:
        oid = ObjectId(branch_id)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid branch_id format"
        )
    background_tasks.add_task(
        logger.info,
        f"User {current_user.user_id} updating company branch ID: {branch_id}"
    )

    update_data = data.model_dump(exclude_unset=True)
    update_data.update(
        {
            "updated_at": now_vn(),
            "updated_by": ObjectId(current_user.user_id)
        }
    )
    background_tasks.add_task(
        logger.info,
        f"Updating company branch ID: {branch_id} with data: {update_data}"
    )

    company_branch = await CompanyBranch.find_one(
            {"_id": oid, "is_active": True}).update(
            {"$set": update_data},
            return_document=True
        )
    if not company_branch:
        raise HTTPException(status_code=404, detail="Company branch not found")

    return CompanyBranchResponse.model_validate(company_branch)

@router.delete("/company-branches/{branch_id}", status_code=204)
@limiter.limit("3/minute")
async def delete_company_branch(
    request: Request,
    branch_id: str,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(
        require_permission("company_branches:delete")
    ),
):
    try:
        oid = ObjectId(branch_id)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid branch_id format"
        )

    background_tasks.add_task(
        logger.info,
        f"User {current_user.user_id} deleting company branch ID: {branch_id}"
    )

    result = await CompanyBranch.find_one(
        {"_id": oid, "is_active": True}
    ).update(
        {
            "$set": {
                "is_active": False,
                "updated_at": now_vn(),
                "updated_by": ObjectId(current_user.user_id)
            }
        }
    )

    if not result:
        raise HTTPException(status_code=404, detail="Company branch not found")