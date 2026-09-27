"""Uncached authorization queries shared by legacy and recruitment services."""
from app.core.transactions import current_session
from bson import ObjectId

from app.models.company import Company
from app.models.company_branch import CompanyBranch
from app.models.user import User
from app.models.user_company import UserCompany
from app.utils.time import now_utc


def active_membership_filter() -> dict:
    now = now_utc()
    return {
        "is_active": True,
        "$and": [
            {"$or": [{"start_date": None}, {"start_date": {"$lte": now}}]},
            {"$or": [{"end_date": None}, {"end_date": {"$gt": now}}]},
        ],
    }


async def branch_role(user_id: str, branch_id: str) -> str | None:
    if not ObjectId.is_valid(user_id) or not ObjectId.is_valid(branch_id):
        return None
    user = await User.get(ObjectId(user_id), session=current_session())
    branch = await CompanyBranch.get(ObjectId(branch_id), session=current_session())
    if not user or not user.is_active or not branch or not branch.is_active:
        return None
    company = await Company.get(branch.company_id, session=current_session())
    if not company or not company.is_active:
        return None
    if user.is_superuser or company.user_id == user.id:
        return "owner"
    link = await UserCompany.find_one(
        {
            "user_id": user.id,
            "company_branch_id": branch.id,
            **active_membership_filter(),
        },
        session=current_session(),
    )
    # A legacy membership labelled owner is not the company's actual owner.
    return ("admin" if link.role == "owner" else link.role) if link else None
