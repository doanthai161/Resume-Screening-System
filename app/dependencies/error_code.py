from enum import Enum
from typing import Dict, Any
from datetime import datetime, timezone

class ErrorCode(str, Enum):
    """Standardized error codes for the application"""
    
    INVALID_CREDENTIALS = "AUTH_1001"
    TOKEN_EXPIRED = "AUTH_1002"
    TOKEN_INVALID = "AUTH_1003"
    INVALID_REFRESH_TOKEN = "AUTH_1004"
    USER_NOT_FOUND = "AUTH_1005"
    USER_INACTIVE = "AUTH_1006"
    INCORRECT_PASSWORD = "AUTH_1007"
    ACCOUNT_LOCKED = "AUTH_1008"
    TOO_MANY_ATTEMPTS = "AUTH_1009"
    
    FORBIDDEN = "AUTH_1101"
    INSUFFICIENT_PERMISSIONS = "AUTH_1102"
    ROLE_REQUIRED = "AUTH_1103"
    
    VALIDATION_ERROR = "VAL_1201"
    REQUIRED_FIELD = "VAL_1202"
    INVALID_EMAIL = "VAL_1203"
    INVALID_PHONE = "VAL_1204"
    INVALID_DATE = "VAL_1205"
    FILE_TOO_LARGE = "VAL_1206"
    INVALID_FILE_TYPE = "VAL_1207"
    
    RESOURCE_NOT_FOUND = "RES_1301"
    RESOURCE_ALREADY_EXISTS = "RES_1302"
    RESOURCE_CONFLICT = "RES_1303"
    RESOURCE_LIMIT_EXCEEDED = "RES_1304"
    
    INTERNAL_ERROR = "SYS_1401"
    SERVICE_UNAVAILABLE = "SYS_1402"
    DATABASE_ERROR = "SYS_1403"
    EXTERNAL_API_ERROR = "SYS_1404"
    
    INVALID_OPERATION = "BIZ_1501"
    BUSINESS_RULE_VIOLATION = "BIZ_1502"
    
    RATE_LIMIT_EXCEEDED = "RATE_1601"
    EMAIL_ALREADY_REGISTERED = "EMAIL_1701"
    TOO_MANY_REQUESTS = "RATE_1702"
    INVALID_OTP = "EMAIL_1703"
    OTP_EXPIRED = "EMAIL_1704"

ERROR_DETAILS: Dict[ErrorCode, Dict[str, Any]] = {
    ErrorCode.INVALID_CREDENTIALS: {
        "message": "Invalid authentication credentials",
        "http_status": 401,
    },
    ErrorCode.FORBIDDEN: {
        "message": "You don't have permission to access this resource",
        "http_status": 403,
    },
}

def get_error_response(error_code: ErrorCode, details: str = None) -> Dict[str, Any]:
    """Get standardized error response"""
    error_info = ERROR_DETAILS.get(error_code, {
        "message": "An error occurred",
        "http_status": 500,
    })
    
    return {
        "error": {
            "code": error_code.value,
            "message": error_info["message"],
            "details": details,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    }