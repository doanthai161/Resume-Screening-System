from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, model_validator

from app.models.job_requirement import EmploymentType, JobStatus, WorkMode


class JobRequirementBase(BaseModel):
    user_id: Optional[str] = None
    company_branch_id: str
    title: str = Field(..., min_length=1, max_length=200)
    programming_languages: list[str] = Field(default_factory=list, max_length=100)
    skills_required: list[str] = Field(default_factory=list, max_length=200)
    experience_level: str = Field(..., min_length=1, max_length=50)
    description: Optional[str] = Field(None, max_length=20000)
    employment_type: EmploymentType = EmploymentType.FULL_TIME
    work_mode: WorkMode = WorkMode.ONSITE
    location: Optional[str] = Field(None, max_length=200)
    number_of_openings: int = Field(1, ge=1, le=10000)
    salary_min: Optional[int] = Field(None, ge=0)
    salary_max: Optional[int] = Field(None, ge=0)
    salary_currency: Optional[str] = Field(None, min_length=3, max_length=3)
    salary_period: Optional[str] = Field(None, pattern=r"^(hour|day|month|year)$")
    expiration_time: Optional[datetime] = None
    status: JobStatus = JobStatus.DRAFT

    @model_validator(mode="after")
    def validate_salary(self) -> "JobRequirementBase":
        if self.salary_min is not None and self.salary_max is not None and self.salary_min > self.salary_max:
            raise ValueError("salary_min must be less than or equal to salary_max")
        if (self.salary_min is not None or self.salary_max is not None) and not self.salary_currency:
            raise ValueError("salary_currency is required when salary is provided")
        self.salary_currency = self.salary_currency.upper() if self.salary_currency else None
        return self


class JobRequirementCreate(JobRequirementBase):
    pass


class JobRequirementUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    programming_languages: Optional[list[str]] = Field(None, max_length=100)
    skills_required: Optional[list[str]] = Field(None, max_length=200)
    experience_level: Optional[str] = Field(None, min_length=1, max_length=50)
    description: Optional[str] = Field(None, max_length=20000)
    employment_type: Optional[EmploymentType] = None
    work_mode: Optional[WorkMode] = None
    location: Optional[str] = Field(None, max_length=200)
    number_of_openings: Optional[int] = Field(None, ge=1, le=10000)
    salary_min: Optional[int] = Field(None, ge=0)
    salary_max: Optional[int] = Field(None, ge=0)
    salary_currency: Optional[str] = Field(None, min_length=3, max_length=3)
    salary_period: Optional[str] = Field(None, pattern=r"^(hour|day|month|year)$")
    expiration_time: Optional[datetime] = None
    status: Optional[JobStatus] = None
    is_open: Optional[bool] = None  # legacy API compatibility
    is_active: Optional[bool] = None


class JobRequirementResponse(BaseModel):
    id: str
    user_id: str
    company_branch_id: str
    title: str
    programming_languages: list[str]
    skills_required: list[str]
    experience_level: str
    description: Optional[str]
    employment_type: EmploymentType
    work_mode: WorkMode
    location: Optional[str]
    number_of_openings: int
    salary_min: Optional[int]
    salary_max: Optional[int]
    salary_currency: Optional[str]
    salary_period: Optional[str]
    expiration_time: Optional[datetime]
    status: JobStatus
    is_open: bool
    is_active: bool
    published_at: Optional[datetime]
    closed_at: Optional[datetime]
    version: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class JobRequirementListResponse(BaseModel):
    job_requirements: list[JobRequirementResponse]
    total: int
    skip: int
    limit: int
