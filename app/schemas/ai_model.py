from typing import Literal, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator


ScreeningModelType = Literal["scoring", "skill_matcher"]


class AIModelResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str = Field(validation_alias=AliasChoices("id", "_id"))
    name: str
    provider: str
    model_id: str
    version: str
    description: Optional[str] = None

    @field_validator("id", mode="before")
    @classmethod
    def stringify_id(cls, value):
        return str(value)


class AIModelListResponse(BaseModel):
    items: list[AIModelResponse]
    total: int
    page: int
    size: int
