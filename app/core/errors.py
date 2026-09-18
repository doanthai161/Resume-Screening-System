from enum import Enum
from typing import Any, Optional

class ErrorCodes(str, Enum):
    INTERNAL = "INTERNAL_ERROR"
    VALIDATION = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    BAD_REQUEST = "BAD_REQUEST"
    CONFLICT = "CONFLICT"
    RATE_LIMIT = "RATE_LIMIT"
    TOKEN_EXHAUSTED = "TOKEN_EXHAUSTED"
    PERMISSION_DENIED = "PERMISSION_DENIED"

class CustomError(Exception):
    def __init__(
        self,
        code: ErrorCodes,
        message: str,
        status_code: int = 400,
        details: Optional[Any] = None
    ):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details
        super().__init__(self.message)
