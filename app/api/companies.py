from bson import ObjectId
from fastapi import Request, BackgroundTasks, APIRouter, HTTPException, status, FastAPI, Depends
from app.utils.time import now_vn
from app.models.company import Company
from app.schemas.company import CompanyCreate, CompanyResponse, CompanyUpdate, CompanyListResponse
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

@router.post("/companies", response_model=CompanyResponse, status_code=201)
@limiter.limit("3/minute")
async def create_company(
    request: Request,
    data: CompanyCreate,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(
        require_permission("companies:create")
    ),
):
    background_tasks.add_task(
        logger.info,
        f"User {current_user.user_id} creating company: {data.name}"
    )

    company = Company(
        name=data.name,
        description=data.description,
        company_short_name=data.company_short_name,
        tax_code=data.tax_code,
        email=data.email,
        logo_url=data.logo_url,
        website=data.website,
        is_active=True,
        created_at=now_vn(),
        created_by=current_user.user_id,
    )

    try:
        await company.insert()
    except Exception as exc:
        if "E11000" in str(exc):
            raise HTTPException(
                status_code=409,
                detail="Company already exists"
            )
        raise

    return CompanyResponse.model_validate(company)

    
@router.get("/list-companies", response_model=CompanyListResponse)
@limiter.limit("10/minute")
async def list_companies(
    request: Request,
    background_tasks: BackgroundTasks,
    page: 1,
    size: 10,
    current_user: CurrentUser = Depends(get_current_user)
):
    background_tasks.add_task(
        logger.info,
        f"User {current_user.user_id} listing companies: page {page}, size {size}"
    )

    skip = (page - 1) * size
    companies_cursor = Company.find({"is_active": True}).skip(skip).limit(size)
    companies = await companies_cursor.to_list(length=size)

    return CompanyListResponse(
        companies=[CompanyResponse.model_validate(company) for company in companies]
    )

@router.patch("/update-company/{company_id}", response_model=CompanyResponse)
@limiter.limit("5/minute")
async def update_company(
    request: Request,
    company_id: str,
    data: CompanyUpdate,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(
        require_permission("companies:edit")
    ),
):
    try:
        oid = ObjectId(company_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid company_id")

    update_data = data.model_dump(exclude_unset=True)

    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")

    update_data.update({
        "updated_at": now_vn(),
        "updated_by": current_user.user_id
    })

    background_tasks.add_task(
        logger.info,
        f"User {current_user.user_id} updating company {company_id}: {list(update_data.keys())}"
    )

    company = await Company.find_one(
        {"_id": oid, "is_active": True}).update(
        {"$set": update_data},
        return_document=True
    )

    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    return CompanyResponse.model_validate(company)


@router.get("/get-company/{company_id}", response_model=CompanyResponse)
@limiter.limit("10/minute")
async def get_company(
    request: Request,
    company_id: str,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(require_permission("companies:view")),
):
    try:
        oid = ObjectId(company_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid company_id")

    background_tasks.add_task(
        logger.info,
        f"Fetching company ID: {company_id}"
    )

    company = await Company.find_one(
        {"_id": oid, "is_active": True}
    )

    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    return CompanyResponse.model_validate(company)


@router.delete("/delete-company/{company_id}", status_code=200)
@limiter.limit("5/minute")
async def delete_company(
    request: Request,
    company_id: str,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(
        require_permission("companies:delete")
    ),
):
    try:
        oid = ObjectId(company_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid company_id")

    background_tasks.add_task(
        logger.info,
        f"User {current_user.id} deleting company {company_id}"
    )

    result = await Company.find_one(
        {"_id": oid, "is_active": True}
    ).update(
        {
            "$set": {
                "is_active": False,
                "updated_at": now_vn(),
                "updated_by": current_user.user_id,
            }
        }
    )

    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Company not found or already deleted")

    background_tasks.add_task(
        logger.info,
        f"Company {company_id} soft-deleted by user {current_user.user_id}"
    )

    return {"message": "Company deleted successfully"}
