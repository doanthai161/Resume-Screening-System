from typing import Tuple, List, Any
from bson import ObjectId
from app.repositories.company_repository import CompanyRepository
from app.schemas.company import CompanyCreate, CompanyUpdate
from app.models.company import Company
from app.core.errors import CustomError, ErrorCodes
from fastapi import status

class CompanyService:
    @staticmethod
    async def create_company(company_data: CompanyCreate, owner_id: str) -> Company:
        try:
            company = await CompanyRepository.create_company(
                company_data=company_data,
                owner_id=owner_id
            )
            return company
        except ValueError as e:
            raise CustomError(ErrorCodes.VALIDATION, str(e), status_code=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            raise CustomError(
                ErrorCodes.INTERNAL,
                "Failed to create company",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @staticmethod
    async def list_companies(page: int, size: int) -> Tuple[List[Company], int]:
        try:
            if page < 1: page = 1
            if size < 1 or size > 100: size = 10
            
            companies, total = await CompanyRepository.list_all_active_companies(
                page=page,
                size=size
            )
            return companies, total
        except Exception as e:
            raise CustomError(
                ErrorCodes.INTERNAL,
                "Failed to list companies",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @staticmethod
    async def get_company(company_id: str, user_id: str, is_superuser: bool, permissions: List[str]) -> Company:
        company = await CompanyRepository.get_company(company_id)
        if not company:
            raise CustomError(
                ErrorCodes.NOT_FOUND,
                "Company not found",
                status_code=status.HTTP_404_NOT_FOUND
            )
            
        user_companies = await CompanyRepository.get_user_companies(user_id)
        has_access = any(str(c.id) == company_id for c in user_companies)
        permission_names = [getattr(p, "name", p) for p in permissions]

        if not has_access and not (is_superuser or "admin" in permission_names):
            raise CustomError(
                ErrorCodes.FORBIDDEN,
                "Access denied",
                status_code=status.HTTP_403_FORBIDDEN
            )
            
        return company

    @staticmethod
    async def update_company(company_id: str, update_data: CompanyUpdate, user_id: str) -> Company:
        user_role = await CompanyRepository.get_user_company_role(
            user_id=user_id,
            company_id=company_id
        )
        if user_role not in ["owner", "admin"]:
            raise CustomError(
                ErrorCodes.FORBIDDEN,
                "Only the company owner or an admin can update this company",
                status_code=status.HTTP_403_FORBIDDEN
            )

        company = await CompanyRepository.update_company(company_id, update_data)
        if not company:
            raise CustomError(
                ErrorCodes.NOT_FOUND,
                "Company not found",
                status_code=status.HTTP_404_NOT_FOUND
            )
            
        return company

    @staticmethod
    async def delete_company(company_id: str, user_id: str) -> bool:
        try:
            success = await CompanyRepository.delete_company(
                company_id=company_id,
                user_id=user_id
            )
            
            if not success:
                raise CustomError(
                    ErrorCodes.NOT_FOUND,
                    "Company not found or unauthorized",
                    status_code=status.HTTP_404_NOT_FOUND
                )
            
            return success
        except ValueError as e:
            raise CustomError(
                ErrorCodes.FORBIDDEN,
                str(e),
                status_code=status.HTTP_403_FORBIDDEN
            )
        except CustomError:
            raise
        except Exception as e:
            raise CustomError(
                ErrorCodes.INTERNAL,
                "Failed to delete company",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
