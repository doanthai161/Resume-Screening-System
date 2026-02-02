from typing import List, Optional
from app.models.user_company import UserCompany
from app.core.monitoring import monitor_db_operation, monitor_cache_operation
import logging

logger = logging.getLogger(__name__)

class UserCompanyRepository:
    @staticmethod
    @monitor_db_operation("user_company_assign")
    async def assign_user_to_branch(
        user_id: str,
        company_branch_id: str,
        assigned_by: str,
        role: str = "member",
        permissions: Optional[List[str]] = None
    ) -> Optional[UserCompany]:
        try:
            user_company = UserCompany(
                user_id=user_id,
                company_branch_id=company_branch_id,
                created_by=assigned_by,
                role=role,
                permissions=permissions
            )
            await user_company.save()
            return user_company
        except Exception as e:
            logger.error(f"Error assigning user to branch: {e}")
            return None

    @staticmethod
    @monitor_db_operation("user_company_unassign")
    async def unassign_user_from_branch(
        user_id: str,
        company_branch_id: str,
        unassigned_by: str
    ) -> bool:
        try:
            user_company = await UserCompany.find_one(UserCompany.user_id == user_id, UserCompany.company_branch_id == company_branch_id)
            if user_company:
                await user_company.delete()
                return True
            return False
        except Exception as e:
            logger.error(f"Error unassigning user from branch: {e}")
            return False
        
    @staticmethod
    @monitor_db_operation("user_company_get")
    @monitor_cache_operation("user_company_get")
    async def get_assignment(assignment_id: str) -> Optional[UserCompany]:
        try:
            user_company = await UserCompany.find_one(UserCompany.id == assignment_id)
            if user_company:
                return user_company
            return None
        except Exception as e:
            logger.error(f"Error getting assignment: {e}")
            return None