from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel

class JobBase(BaseModel):
    job_title: str
    description: str
    requirements: Optional[str]
    skills_required: List[str]
    location: Optional[str]
    salary_min: Optional[int]
    salary_max: Optional[int]
    company_name: Optional[str]

class JobCreate(JobBase):
    pass

class JobUpdate(BaseModel):
    job_title: Optional[str]
    description: Optional[str]
    requirements: Optional[str]
    skills_required: Optional[List[str]]
    location: Optional[str]
    salary_min: Optional[int]
    salary_max: Optional[int]
    company_name: Optional[str]
    active: Optional[bool]

class JobResponse(JobBase):
    id: str
    employer_id: Optional[str]
    active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        orm_mode = True
        allow_population_by_field_name = True


class JobListResponse(BaseModel):
    jobs: list[JobResponse]
    total: int
    page:int
    size: int