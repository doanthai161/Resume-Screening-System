from app.core.transactions import current_session, transactional
import math
from typing import List, Dict, Any
import time
from fastapi import status
from app.repositories.user_company_repository import UserCompanyRepository
from app.repositories.user_repository import UserRepository
from app.repositories.company_repository import CompanyRepository
from app.schemas.user import UserResponse
from app.schemas.company_branch import CompanyBranchResponse
from app.schemas.user_company import (
    AssignUserToCompanyBranch,
    UserCompanyResponse,
    UserCompanyListResponse,
    UserCompanyStats,
)
from app.core.errors import CustomError, ErrorCodes
from app.core.monitoring import record_business_metric
from app.logs.logging_config import logger
from bson import ObjectId
from app.core.tenant_policy import branch_role
from app.models.company import Company
from app.models.company_branch import CompanyBranch
from app.models.user_company import UserCompany


class UserCompanyService:
    @staticmethod
    async def _authorize_change(
        branch_id: str,
        target_id: str,
        caller_id: str,
        new_role: str | None = None,
        old_role: str | None = None,
    ) -> None:
        if not all(
            ObjectId.is_valid(value) for value in (branch_id, target_id, caller_id)
        ):
            raise CustomError(ErrorCodes.VALIDATION, "Invalid membership ID", 422)
        branch_id, target_id, caller_id = (
            str(ObjectId(value)) for value in (branch_id, target_id, caller_id)
        )
        role = await branch_role(caller_id, branch_id)
        ranks = {"member": 1, "manager": 2, "admin": 3, "owner": 4}
        if role not in {"owner", "admin", "manager"}:
            raise CustomError(ErrorCodes.FORBIDDEN, "Membership management denied", 403)
        branch = await CompanyBranch.get(ObjectId(branch_id), session=current_session())
        company = await Company.get(branch.company_id, session=current_session())
        if target_id == caller_id or str(company.user_id) == target_id:
            raise CustomError(
                ErrorCodes.FORBIDDEN, "Use a separate ownership transfer operation", 403
            )
        if new_role is not None and new_role not in {"member", "manager", "admin"}:
            raise CustomError(ErrorCodes.BAD_REQUEST, "Invalid assignable role", 400)
        if role != "owner" and any(
            ranks.get(value, 99) >= ranks[role]
            for value in (new_role, old_role)
            if value is not None
        ):
            raise CustomError(
                ErrorCodes.FORBIDDEN, "Cannot manage an equal or higher role", 403
            )

    @staticmethod
    async def _to_response(assignment: Any) -> UserCompanyResponse:
        user = await UserRepository.get_user(str(assignment.user_id))
        branch = await CompanyRepository.get_company_branch(
            str(assignment.company_branch_id)
        )

        if not user or not branch:
            raise CustomError(
                ErrorCodes.NOT_FOUND,
                "User or branch not found",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        return UserCompanyResponse(
            id=str(assignment.id),
            user=UserResponse(
                id=str(user.id),
                email=user.email,
                full_name=user.full_name,
                phone_number=user.phone_number,
                address=user.address,
            ),
            company_branch=CompanyBranchResponse(
                id=str(branch.id),
                company_id=str(branch.company_id),
                bussiness_type=branch.bussiness_type,
                branch_name=branch.branch_name,
                phone_number=branch.phone_number,
                address=branch.address,
                description=branch.description,
                company_type=branch.company_type,
                company_industry=branch.company_industry,
                country=branch.country,
                company_size=branch.company_size,
                working_days=branch.working_days,
                overtime_policy=branch.overtime_policy,
            ),
            role=assignment.role,
            permissions=assignment.permissions or [],
            is_active=assignment.is_active,
            created_at=assignment.created_at,
            updated_at=assignment.updated_at,
        )

    @staticmethod
    @transactional
    async def assign_user(
        data: AssignUserToCompanyBranch, current_user_id: str
    ) -> Dict[str, Any]:
        if data.permissions:
            raise CustomError(
                ErrorCodes.FORBIDDEN, "Direct permission grants are not supported", 403
            )
        prior = await UserCompany.find_one(
            {
                "user_id": ObjectId(data.user_id),
                "company_branch_id": ObjectId(data.company_branch_id),
            },
            session=current_session(),
        )
        await UserCompanyService._authorize_change(
            data.company_branch_id,
            data.user_id,
            current_user_id,
            data.role or "member",
            prior.role if prior else None,
        )
        branch = await CompanyRepository.get_company_branch(data.company_branch_id)
        if not branch or not branch.is_active:
            raise CustomError(
                ErrorCodes.NOT_FOUND,
                "Company branch not found or inactive",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        user = await UserRepository.get_user(data.user_id)
        if not user or not user.is_active:
            raise CustomError(
                ErrorCodes.NOT_FOUND,
                "User not found or inactive",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        assignment = await UserCompanyRepository.assign_user_to_branch(
            user_id=data.user_id,
            company_branch_id=data.company_branch_id,
            assigned_by=current_user_id,
            role=data.role or "member",
            permissions=data.permissions,
        )

        if not assignment:
            raise CustomError(
                ErrorCodes.BAD_REQUEST,
                "Failed to assign user to branch",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        record_business_metric(
            "user_assigned_to_branch",
            tags={
                "company_branch_id": data.company_branch_id,
                "assigned_by": current_user_id,
                "role": data.role or "member",
            },
        )

        return {
            "message": "User assigned to company branch successfully",
            "assignment_id": str(assignment.id),
            "user_id": data.user_id,
            "company_branch_id": data.company_branch_id,
            "role": data.role or "member",
            "timestamp": time.time(),
        }

    @staticmethod
    @transactional
    async def unassign_user(
        data: AssignUserToCompanyBranch, current_user_id: str
    ) -> Dict[str, Any]:
        prior = await UserCompany.find_one(
            {
                "user_id": ObjectId(data.user_id),
                "company_branch_id": ObjectId(data.company_branch_id),
                "is_active": True,
            },
            session=current_session(),
        )
        await UserCompanyService._authorize_change(
            data.company_branch_id,
            data.user_id,
            current_user_id,
            old_role=prior.role if prior else None,
        )
        branch = await CompanyRepository.get_company_branch(data.company_branch_id)
        if not branch:
            raise CustomError(
                ErrorCodes.NOT_FOUND,
                "Company branch not found",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        success = await UserCompanyRepository.unassign_user_from_branch(
            user_id=data.user_id,
            company_branch_id=data.company_branch_id,
            unassigned_by=current_user_id,
        )

        if not success:
            raise CustomError(
                ErrorCodes.NOT_FOUND,
                "User assignment not found",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        record_business_metric(
            "user_unassigned_from_branch",
            tags={
                "company_branch_id": data.company_branch_id,
                "unassigned_by": current_user_id,
            },
        )

        return {
            "message": "User unassigned from company branch successfully",
            "user_id": data.user_id,
            "company_branch_id": data.company_branch_id,
            "timestamp": time.time(),
        }

    @staticmethod
    @transactional
    async def delete_assignment(
        assignment_id: str, current_user: Any
    ) -> Dict[str, Any]:
        target = await UserCompanyRepository.get_assignment(assignment_id)
        if not target:
            raise CustomError(ErrorCodes.NOT_FOUND, "Assignment not found", 404)
        await UserCompanyService._authorize_change(
            str(target.company_branch_id),
            str(target.user_id),
            str(current_user.id),
            old_role=target.role,
        )
        if not current_user.is_superuser:
            assignment = await UserCompanyRepository.get_assignment(assignment_id)
            if assignment:
                user_role = await UserCompanyRepository.get_user_role_in_branch(
                    user_id=str(current_user.id),
                    company_branch_id=str(assignment.company_branch_id),
                )

                if not user_role or user_role not in ["owner", "admin"]:
                    raise CustomError(
                        ErrorCodes.FORBIDDEN,
                        "Only owners and admins can delete assignments",
                        status_code=status.HTTP_403_FORBIDDEN,
                    )

        success = await UserCompanyRepository.delete_assignment(
            assignment_id=assignment_id, deleted_by=str(current_user.id)
        )

        if not success:
            raise CustomError(
                ErrorCodes.NOT_FOUND,
                "Assignment not found",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        record_business_metric(
            "user_company_assignment_deleted", tags={"deleted_by": str(current_user.id)}
        )

        return {
            "message": "User-company assignment deleted permanently",
            "assignment_id": assignment_id,
            "timestamp": time.time(),
        }

    @staticmethod
    async def get_assignment(
        assignment_id: str, current_user: Any
    ) -> UserCompanyResponse:
        assignment = await UserCompanyRepository.get_assignment(assignment_id)
        if not assignment:
            raise CustomError(
                ErrorCodes.NOT_FOUND,
                "Assignment not found",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        has_access = await CompanyRepository.validate_user_access(
            user_id=str(current_user.id),
            company_branch_id=str(assignment.company_branch_id),
        )

        if not has_access and not current_user.is_superuser:
            raise CustomError(
                ErrorCodes.FORBIDDEN,
                "Access denied",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        return await UserCompanyService._to_response(assignment)

    @staticmethod
    async def list_branch_users(
        company_branch_id: str,
        active_only: bool,
        page: int,
        size: int,
        current_user: Any,
    ) -> UserCompanyListResponse:
        has_access = await CompanyRepository.validate_user_access(
            user_id=str(current_user.id), company_branch_id=company_branch_id
        )

        if not has_access and not current_user.is_superuser:
            raise CustomError(
                ErrorCodes.FORBIDDEN,
                "Access denied",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        if page < 1:
            page = 1
        if size < 1 or size > 100:
            size = 20

        skip = (page - 1) * size

        assignments, total = await UserCompanyRepository.list_branch_assignments(
            company_branch_id=company_branch_id,
            active_only=active_only,
            skip=skip,
            limit=size,
        )

        users_with_details = []
        for assignment in assignments:
            try:
                users_with_details.append(
                    await UserCompanyService._to_response(assignment)
                )
            except CustomError:
                continue

        record_business_metric(
            "branch_users_listed",
            value=len(users_with_details),
            tags={"company_branch_id": company_branch_id, "active_only": active_only},
        )

        total_pages = math.ceil(total / size) if size else 0
        has_next = page < total_pages
        has_previous = page > 1

        return UserCompanyListResponse(
            users=users_with_details,
            total=total,
            page=page,
            size=size,
            total_pages=total_pages,
            has_next=has_next,
            has_previous=has_previous,
            next_page=page + 1 if has_next else page,
            previous_page=page - 1 if has_previous else page,
            total_items=total,
        )

    @staticmethod
    async def list_user_branches(
        user_id: str, active_only: bool, current_user: Any
    ) -> List[UserCompanyResponse]:
        if not ObjectId.is_valid(user_id):
            raise CustomError(ErrorCodes.VALIDATION, "Invalid user ID", 422)
        user_id = str(ObjectId(user_id))
        assignments = (
            await UserCompany.find(
                {
                    "user_id": ObjectId(user_id),
                    **({"is_active": True} if active_only else {}),
                },
                session=current_session(),
            )
            .limit(100)
            .to_list()
        )
        results = []
        for assignment in assignments:
            role = await branch_role(
                str(current_user.id), str(assignment.company_branch_id)
            )
            if not current_user.is_superuser and not (
                role
                and (
                    user_id == str(current_user.id)
                    or role in {"owner", "admin", "manager"}
                )
            ):
                continue
            try:
                results.append(await UserCompanyService._to_response(assignment))
            except CustomError:
                continue
        if (
            not results
            and user_id != str(current_user.id)
            and not current_user.is_superuser
        ):
            raise CustomError(ErrorCodes.FORBIDDEN, "Access denied", 403)

        record_business_metric(
            "user_branches_listed",
            value=len(results),
            tags={"user_id": user_id, "active_only": active_only},
        )

        return results

    @staticmethod
    async def get_branch_assignment_stats(
        company_branch_id: str, current_user: Any
    ) -> UserCompanyStats:
        has_access = await CompanyRepository.validate_user_access(
            user_id=str(current_user.id), company_branch_id=company_branch_id
        )

        if not has_access and not current_user.is_superuser:
            raise CustomError(
                ErrorCodes.FORBIDDEN,
                "Access denied",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        stats = await UserCompanyRepository.get_branch_assignment_stats(
            company_branch_id
        )

        record_business_metric(
            "branch_stats_retrieved", tags={"company_branch_id": company_branch_id}
        )

        return stats

    @staticmethod
    @transactional
    async def update_assignment_role(
        assignment_id: str, role: str, current_user: Any
    ) -> UserCompanyResponse:
        assignment = await UserCompanyRepository.get_assignment(assignment_id)
        if not assignment:
            raise CustomError(
                ErrorCodes.NOT_FOUND,
                "Assignment not found",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        await UserCompanyService._authorize_change(
            str(assignment.company_branch_id),
            str(assignment.user_id),
            str(current_user.id),
            role,
            assignment.role,
        )

        user_role = await UserCompanyRepository.get_user_role_in_branch(
            user_id=str(current_user.id),
            company_branch_id=str(assignment.company_branch_id),
        )

        if not user_role or user_role not in ["owner", "admin", "manager"]:
            raise CustomError(
                ErrorCodes.FORBIDDEN,
                "Only owners, admins and managers can update roles",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        updated_assignment = await UserCompanyRepository.update_assignment_role(
            assignment_id=assignment_id, role=role, updated_by=str(current_user.id)
        )

        if not updated_assignment:
            raise CustomError(
                ErrorCodes.NOT_FOUND,
                "Assignment not found",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        record_business_metric(
            "assignment_role_updated",
            tags={
                "assignment_id": assignment_id,
                "old_role": assignment.role,
                "new_role": role,
                "updated_by": str(current_user.id),
            },
        )

        return await UserCompanyService._to_response(updated_assignment)
