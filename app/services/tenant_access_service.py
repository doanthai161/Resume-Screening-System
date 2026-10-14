from app.core.tenant_policy import active_membership_filter
from app.core.transactions import current_session

from bson import ObjectId
from fastapi import status

from app.core.errors import CustomError, ErrorCodes
from app.core.security import CurrentUser
from app.models.company import Company
from app.models.company_branch import CompanyBranch
from app.models.user_company import UserCompany
from app.models.user import User


class TenantAccessService:
    """Tenant authorization always reads the source of truth.

    Authorization is deliberately not cached: stale membership data can leak
    tenant records after a user's access is revoked.
    """

    @staticmethod
    async def require_company_access(
        current_user: CurrentUser, company_id: str
    ) -> Company:
        if not ObjectId.is_valid(company_id):
            raise CustomError(
                ErrorCodes.VALIDATION,
                "Invalid company ID",
                status.HTTP_422_UNPROCESSABLE_ENTITY,
            )

        company = await Company.get(ObjectId(company_id), session=current_session())
        if not company or not company.is_active:
            raise CustomError(
                ErrorCodes.NOT_FOUND, "Company not found", status.HTTP_404_NOT_FOUND
            )

        user = await User.get(ObjectId(current_user.user_id), session=current_session()) if current_user.user_id and ObjectId.is_valid(current_user.user_id) else None
        if not user or not user.is_active:
            raise CustomError(ErrorCodes.FORBIDDEN, "Active company user required", 403)
        if user.is_superuser or company.user_id == user.id:
            return company

        branches = await CompanyBranch.find(
            {"company_id": company.id, "is_active": True}, session=current_session()
        ).to_list()
        branch_ids = [branch.id for branch in branches]
        if not branch_ids:
            raise CustomError(
                ErrorCodes.FORBIDDEN, "Company access denied", status.HTTP_403_FORBIDDEN
            )

        membership = await UserCompany.find_one(
            {
                "user_id": ObjectId(current_user.user_id),
                "company_branch_id": {"$in": branch_ids},
                **active_membership_filter(),
            },
            session=current_session(),
        )
        if not membership:
            raise CustomError(
                ErrorCodes.FORBIDDEN, "Company access denied", status.HTTP_403_FORBIDDEN
            )
        return company

    @staticmethod
    async def authorized_branch_ids(current_user: CurrentUser, company_id: str) -> list[ObjectId]:
        company = await TenantAccessService.require_company_access(current_user, company_id)
        user = await User.get(ObjectId(current_user.user_id), session=current_session())
        branches = await CompanyBranch.find({"company_id": company.id, "is_active": True}, session=current_session()).to_list()
        branch_ids = [branch.id for branch in branches]
        if user.is_superuser or company.user_id == user.id:
            return branch_ids
        links = await UserCompany.find({"user_id": user.id, "company_branch_id": {"$in": branch_ids},
                                        **active_membership_filter()}, session=current_session()).to_list()
        return list({link.company_branch_id for link in links})
