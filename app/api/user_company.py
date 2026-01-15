from bson import ObjectId
from fastapi import Request, BackgroundTasks, APIRouter, HTTPException, status, FastAPI, Depends
from app.utils.time import now_vn
from app.models.company_branch import CompanyBranch
from app.models.user import User
from app.models.user_company import UserCompany
from app.schemas.user_company import AssignUserToCompanyBranch
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

@router.post("/assign-user-to-company-branch", status_code=201)
@limiter.limit("3/minute")
async def assign_user_to_company_branch(
    request: Request,
    data: AssignUserToCompanyBranch,
    current_user: CurrentUser = Depends(require_permission("companies:create")),
):
    try:
        uid = ObjectId(data.user_id)
        cbid = ObjectId(data.company_branch_id)
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid user_id or company_branch_id"
        )

    user = await User.find_one(
        {"_id": uid, "is_active": True}
    )
    if not user:
        raise HTTPException(404, "User not found")

    company_branch = await CompanyBranch.find_one(
        {"_id": cbid, "is_active": True}
    )
    if not company_branch:
        raise HTTPException(404, "Company branch not found")

    existed = await UserCompany.find_one(
        {
            "user_id": uid,
            "company_branch_id": cbid,
            "is_active": True
        }
    )
    if existed:
        raise HTTPException(
            status_code=409,
            detail="User already assigned to this company branch"
        )

    user_company_branch = UserCompany(
        user_id=uid,
        company_branch_id=cbid,
        created_by=ObjectId(current_user.user_id),
        created_at=now_vn(),
        is_active=True
    )
    await user_company_branch.save()

    logger.info(
        f"User {uid} assigned to company_branch {cbid} by {current_user.user_id}"
    )

    return {
        "message": "User assigned to company branch successfully"
    }

@router.post("/unassign-user-from-company-branch", status_code=200)
@limiter.limit("3/minute")
async def unassign_user_from_company_branch(
    request: Request,
    data: AssignUserToCompanyBranch,
    current_user: CurrentUser = Depends(require_permission("companies:edit")),
):
    try:
        uid = ObjectId(data.user_id)
        cbid = ObjectId(data.company_branch_id)
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid user_id or company_branch_id"
        )

    user_company = await UserCompany.find_one(
        {
            "user_id": uid,
            "company_branch_id": cbid,
            "is_active": True
        }
    )

    if not user_company:
        raise HTTPException(
            status_code=409,
            detail="User is not assigned to this company branch"
        )

    user_company.is_active = False
    user_company.updated_at = now_vn()
    user_company.updated_by = ObjectId(current_user.user_id)

    await user_company.save()

    logger.info(
        f"User {uid} unassigned from company_branch {cbid} by {current_user.user_id}"
    )

    return {
        "message": "User unassigned from company branch successfully"
    }

@router.delete("/delete-user-from-company-branch", status_code=200)
@limiter.limit("2/minute")
async def delete_user_from_company_branch(
    request: Request,
    data: AssignUserToCompanyBranch,
    current_user: CurrentUser = Depends(
        require_permission("companies:delete")
    ),
):
    try:
        uid = ObjectId(data.user_id)
        cbid = ObjectId(data.company_branch_id)
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid user_id or company_branch_id"
        )

    user_company = await UserCompany.find_one(
        {
            "user_id": uid,
            "company_branch_id": cbid
        }
    )

    if not user_company:
        raise HTTPException(
            status_code=404,
            detail="User-company assignment not found"
        )
    await user_company.delete()

    logger.warning(
        f"HARD DELETE user_company: user={uid}, branch={cbid}, by={current_user.user_id}"
    )

    return {
        "message": "User-company assignment deleted permanently"
    }
