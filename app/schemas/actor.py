from typing import Optional
from pydantic import BaseModel

class ActorCreate(BaseModel):
    name: str
    description: Optional[str] = None

class ActorUpdate(BaseModel):
    name: Optional[str]
    description: Optional[str]

class ActorResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]

class ActorListResponse(BaseModel):
    actors: list[ActorResponse]
    total: int
    page: int
    size: int