from typing import List
from fastapi import status
from app.repositories.company_branch_repository import CompanyBranchRepository
from app.repositories.company_repository import CompanyRepository
from app.schemas.company_branch import CompanyBranchCreate
from app.models.company_branch import CompanyBranch
from app.core.errors import CustomError, ErrorCodes

class CompanyBranchService:
    @staticmethod
    async def create_company_branch(
        company_id: str,
        branch_data: CompanyBranchCreate,
        user_id: str,
        is_superuser: bool = False
    ) -> CompanyBranch:
        try:
            if not is_superuser:
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
            user_branches = await CompanyBranchRepository.get_user_company_branches(user_id)
            has_access = any(str(b.company_id) == company_id for b in user_branches)

            permission_names = [getattr(p, "name", p) for p in permissions]

            if not has_access and not (is_superuser or "admin" in permission_names):
                raise CustomError(
                    ErrorCodes.FORBIDDEN,
                    "Access denied",
                    status_code=status.HTTP_403_FORBIDDEN
                )
            
            branches = await CompanyBranchRepository.get_company_branches(company_id)
            return branches
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
            branch = await CompanyBranchRepository.get_company_branch(branch_id)
            if not branch:
                raise CustomError(
                    ErrorCodes.NOT_FOUND,
                    "Branch not found",
                    status_code=status.HTTP_404_NOT_FOUND
                )

            if str(branch.company_id) != company_id:
                raise CustomError(
                    ErrorCodes.BAD_REQUEST,
                    "Branch does not belong to specified company",
                    status_code=status.HTTP_400_BAD_REQUEST
                )

            if not is_superuser:
                has_access = await CompanyRepository.validate_user_access(
                    user_id=user_id,
                    company_branch_id=branch_id
                )
                if not has_access:
                    raise CustomError(
                        ErrorCodes.FORBIDDEN,
                        "Access denied",
                        status_code=status.HTTP_403_FORBIDDEN
                    )

            return branch
        except CustomError:
            raise
        except Exception as e:
            raise CustomError(
                ErrorCodes.INTERNAL,
                "Failed to get branch",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
