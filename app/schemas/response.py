from typing import Generic, TypeVar, Optional, Any
from pydantic import BaseModel, Field

T = TypeVar("T")

class ApiResponse(BaseModel, Generic[T]):
    success: bool = Field(..., description="Indicates if the request was successful")
    data: Optional[T] = Field(None, description="The response data")
    message: Optional[str] = Field(None, description="Optional message")

    @classmethod
    def ok(cls, data: T = None, message: str = "Success") -> "ApiResponse[T]":
        return cls(success=True, data=data, message=message)

    @classmethod
    def error(cls, message: str) -> "ApiResponse[Any]":
        return cls(success=False, data=None, message=message)
