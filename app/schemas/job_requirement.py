from typing import Optional
from pydantic import BaseModel
from bson import ObjectId
from datetime import datetime

class JobRequirementCreate(BaseModel):
    user_id: ObjectId
    company_id: ObjectId
    title: str
    programming_languages: list[str]
    skills_required: list[str]
    experience_level: str
    description: Optional[str] = None
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    expiration_time: Optional[datetime] = None

class JobRequirementUpdate(BaseModel):
    title: Optional[str]
    programming_languages: Optional[list[str]]
    skills_required: Optional[list[str]]
    experience_level: Optional[str]
    description: Optional[str]
    salary_min: Optional[int]
    salary_max: Optional[int]
    expiration_time: Optional[datetime]
    is_open: Optional[bool]
    is_active: Optional[bool]

class JobRequirementResponse(BaseModel):
    id: str
    user_id: str
    company_id: str
    title: str
    programming_languages: list[str]
    skills_required: list[str]
    experience_level: str
    description: Optional[str]
    salary_min: Optional[int]
    salary_max: Optional[int]
    expiration_time: Optional[datetime]
    is_open: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime

class JobRequirementListResponse(BaseModel):
    job_requirements: list[JobRequirementResponse]
    total: int
    page: int
    size: int