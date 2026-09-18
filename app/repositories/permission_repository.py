from typing import List, Tuple, Optional
from bson import ObjectId
from app.models.permission import Permission
from app.schemas.permission import PermissionCreate, PermissionUpdate
from app.utils.time import now_utc

class PermissionRepository:
    @staticmethod
    async def get_by_name(name: str) -> Optional[Permission]:
        return await Permission.find_one({'name': name, 'is_active': True})

    @staticmethod
    async def get_by_id(permission_id: str) -> Optional[Permission]:
        return await Permission.get(ObjectId(permission_id))

    @staticmethod
    async def list_permissions(skip: int, limit: int) -> Tuple[List[Permission], int]:
        permissions = await Permission.find({"is_active": True}).skip(skip).limit(limit).to_list()
        total = await Permission.find({"is_active": True}).count()
        return permissions, total

    @staticmethod
    async def create(data: PermissionCreate) -> Permission:
        permission = Permission(
            name=data.name,
            description=data.description,
            is_active=True,
        )
        await permission.insert()
        return permission

    @staticmethod
    async def update(permission: Permission, data: PermissionUpdate) -> Permission:
        if data.name is not None:
            permission.name = data.name
        if data.description is not None:
            permission.description = data.description
        permission.updated_at = now_utc()
        await permission.save()
        return permission

    @staticmethod
    async def soft_delete(permission: Permission) -> None:
        permission.is_active = False
        permission.updated_at = now_utc()
        await permission.save()
