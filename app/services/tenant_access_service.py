from datetime import datetime, timezone

from bson import ObjectId
from fastapi import status

from app.core.errors import CustomError, ErrorCodes
from app.core.security import CurrentUser
from app.models.company import Company
from app.models.company_branch import CompanyBranch
from app.models.user_company import UserCompany


class TenantAccessService:
    """Tenant authorization always reads the source of truth.

    Authorization is deliberately not cached: stale membership data can leak
    tenant records after a user's access is revoked.
    """

    @staticmethod
    async def require_company_access(current_user: CurrentUser, company_id: str) -> Company:
        if not ObjectId.is_valid(company_id):
            raise CustomError(ErrorCodes.VALIDATION, "Invalid company ID", status.HTTP_422_UNPROCESSABLE_ENTITY)

        company = await Company.get(ObjectId(company_id))
        if not company or not company.is_active:
            raise CustomError(ErrorCodes.NOT_FOUND, "Company not found", status.HTTP_404_NOT_FOUND)

        if current_user.is_superuser or current_user.is_admin or str(company.user_id) == current_user.user_id:
            return company

        branches = await CompanyBranch.find(
            {"company_id": company.id, "is_active": True}
        ).to_list()
        branch_ids = [branch.id for branch in branches]
        if not branch_ids:
            raise CustomError(ErrorCodes.FORBIDDEN, "Company access denied", status.HTTP_403_FORBIDDEN)

        now = datetime.now(timezone.utc)
        membership = await UserCompany.find_one(
            {
                "user_id": ObjectId(current_user.user_id),
                "company_branch_id": {"$in": branch_ids},
                "is_active": True,
                "$and": [
                    {"$or": [{"start_date": None}, {"start_date": {"$lte": now}}]},
                    {"$or": [{"end_date": None}, {"end_date": {"$gte": now}}]},
                ],
            }
        )
        if not membership:
            raise CustomError(ErrorCodes.FORBIDDEN, "Company access denied", status.HTTP_403_FORBIDDEN)
        return company
