from typing import Optional
from pydantic import BaseModel

class PermissionCreate(BaseModel):
    name: str
    description: str

class PermissionUpdate(BaseModel):
    description: Optional[str]

class PermissionResponse(BaseModel):
    id: str
    name: str
    description: str

class PermissionListResponse(BaseModel):
    permissions: list[PermissionResponse]
    total: int
    page: int
    size: int