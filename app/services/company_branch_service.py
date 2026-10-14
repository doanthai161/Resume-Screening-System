from typing import List
from fastapi import status
from app.repositories.company_branch_repository import CompanyBranchRepository
from app.repositories.company_repository import CompanyRepository
from app.schemas.company_branch import CompanyBranchCreate
from app.models.company_branch import CompanyBranch
from app.core.errors import CustomError, ErrorCodes
from app.core.tenant_policy import active_membership_filter, branch_role
from app.core.transactions import current_session
from app.models.company import Company
from app.models.user import User
from app.models.user_company import UserCompany
from bson import ObjectId

class CompanyBranchService:
    @staticmethod
    async def _context(company_id: str, user_id: str):
        if not ObjectId.is_valid(company_id) or not ObjectId.is_valid(user_id):
            raise CustomError(ErrorCodes.VALIDATION, "Invalid company or user ID", 422)
        company = await Company.get(ObjectId(company_id), session=current_session())
        caller = await User.get(ObjectId(user_id), session=current_session())
        if not company or not company.is_active:
            raise CustomError(ErrorCodes.NOT_FOUND, "Company not found", 404)
        if not caller or not caller.is_active:
            raise CustomError(ErrorCodes.FORBIDDEN, "Active user required", 403)
        return company, caller

    @staticmethod
    async def create_company_branch(
        company_id: str,
        branch_data: CompanyBranchCreate,
        user_id: str,
        is_superuser: bool = False
    ) -> CompanyBranch:
        try:
            company, caller = await CompanyBranchService._context(company_id, user_id)
            if not ObjectId.is_valid(branch_data.company_id) or ObjectId(branch_data.company_id) != company.id:
                raise CustomError(ErrorCodes.VALIDATION, "Branch body company must match path company", 422)
            if not caller.is_superuser:
                role = await CompanyRepository.get_user_company_role(user_id, company_id)
                if role not in ["owner", "admin"]:
                    raise CustomError(
                        ErrorCodes.FORBIDDEN,
                        "Only the company owner or an admin can create branches",
                        status_code=status.HTTP_403_FORBIDDEN
                    )

            branch = await CompanyBranchRepository.create_company_branch(
                company_id=company_id,
                branch_data=branch_data,
                created_by_id=user_id
            )
            return branch
        except CustomError:
            raise
        except ValueError as e:
            raise CustomError(ErrorCodes.VALIDATION, str(e), status_code=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            raise CustomError(
                ErrorCodes.INTERNAL,
                "Failed to create company branch",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @staticmethod
    async def list_company_branches(company_id: str, user_id: str, is_superuser: bool, permissions: List[str]) -> List[CompanyBranch]:
        try:
            company, caller = await CompanyBranchService._context(company_id, user_id)
            query = {"company_id": company.id, "is_active": True}
            if not caller.is_superuser and company.user_id != caller.id:
                memberships = await UserCompany.find(
                    {"user_id": caller.id, **active_membership_filter()}, session=current_session()
                ).to_list()
                query["_id"] = {"$in": [item.company_branch_id for item in memberships]}
            allowed = await CompanyBranch.find(query, session=current_session()).to_list()
            if not allowed and not caller.is_superuser and company.user_id != caller.id:
                raise CustomError(ErrorCodes.FORBIDDEN, "Branch access denied", 403)
            allowed_ids = {item.id for item in allowed}
            branches = await CompanyBranchRepository.get_company_branches(str(company.id))
            return [item for item in branches if item.id in allowed_ids and item.company_id == company.id]
        except CustomError:
            raise
        except Exception as e:
            raise CustomError(
                ErrorCodes.INTERNAL,
                "Failed to list company branches",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @staticmethod
    async def get_company_branch(
        company_id: str,
        branch_id: str,
        user_id: str,
        is_superuser: bool = False
    ) -> CompanyBranch:
        try:
            company, caller = await CompanyBranchService._context(company_id, user_id)
            if not ObjectId.is_valid(branch_id):
                raise CustomError(ErrorCodes.VALIDATION, "Invalid branch ID", 422)
            branch = await CompanyBranch.get(ObjectId(branch_id), session=current_session())
            if not branch or not branch.is_active:
                raise CustomError(
                    ErrorCodes.NOT_FOUND,
                    "Branch not found",
                    status_code=status.HTTP_404_NOT_FOUND
                )

            if branch.company_id != company.id:
                raise CustomError(
                    ErrorCodes.BAD_REQUEST,
                    "Branch does not belong to specified company",
                    status_code=status.HTTP_400_BAD_REQUEST
                )

            if not await branch_role(str(caller.id), str(branch.id)):
                raise CustomError(
                    ErrorCodes.FORBIDDEN,
                    "Access denied",
                    status_code=status.HTTP_403_FORBIDDEN
                )

            cached = await CompanyBranchRepository.get_company_branch(str(branch.id))
            # Cached metadata does not grant access or change resource identity.
            return cached if cached and cached.id == branch.id and cached.company_id == company.id else branch
        except CustomError:
            raise
        except Exception as e:
            raise CustomError(
                ErrorCodes.INTERNAL,
                "Failed to get branch",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
