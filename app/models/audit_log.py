from typing import Optional, Dict, Any, List
from datetime import datetime
from enum import Enum
from pydantic import Field
from beanie import Document
from bson import ObjectId
from pymongo import IndexModel, ASCENDING, DESCENDING
from app.utils.time import now_vn


class AuditEventType(str, Enum):
    """Types of audit events"""
    # Authentication events
    USER_LOGIN = "user.login"
    USER_LOGIN_FAILED = "user.login_failed"
    USER_LOGOUT = "user.logout"
    USER_REGISTER = "user.register"
    USER_PASSWORD_CHANGE = "user.password_change"
    USER_PASSWORD_RESET = "user.password_reset"
    USER_EMAIL_VERIFY = "user.email_verify"
    USER_TWO_FACTOR_ENABLE = "user.two_factor_enable"
    USER_TWO_FACTOR_DISABLE = "user.two_factor_disable"
    
    # Authorization events
    PERMISSION_GRANTED = "permission.granted"
    PERMISSION_REVOKED = "permission.revoked"
    ROLE_ASSIGNED = "role.assigned"
    ROLE_REMOVED = "role.removed"
    ACCESS_DENIED = "access.denied"
    
    # User management events
    USER_CREATED = "user.created"
    USER_UPDATED = "user.updated"
    USER_DELETED = "user.deleted"
    USER_DEACTIVATED = "user.deactivated"
    USER_ACTIVATED = "user.activated"
    
    # Company events
    COMPANY_CREATED = "company.created"
    COMPANY_UPDATED = "company.updated"
    COMPANY_DELETED = "company.deleted"
    COMPANY_BRANCH_CREATED = "company_branch.created"
    COMPANY_BRANCH_UPDATED = "company_branch.updated"
    COMPANY_BRANCH_DELETED = "company_branch.deleted"
    
    # Job events
    JOB_REQUIREMENT_CREATED = "job_requirement.created"
    JOB_REQUIREMENT_UPDATED = "job_requirement.updated"
    JOB_REQUIREMENT_DELETED = "job_requirement.deleted"
    JOB_REQUIREMENT_PUBLISHED = "job_requirement.published"
    JOB_REQUIREMENT_CLOSED = "job_requirement.closed"
    
    # Resume events
    RESUME_UPLOADED = "resume.uploaded"
    RESUME_PARSED = "resume.parsed"
    RESUME_DELETED = "resume.deleted"
    RESUME_DOWNLOADED = "resume.downloaded"
    
    # Screening events
    SCREENING_STARTED = "screening.started"
    SCREENING_COMPLETED = "screening.completed"
    SCREENING_FAILED = "screening.failed"
    SCREENING_REVIEWED = "screening.reviewed"
    SCREENING_OVERRIDDEN = "screening.overridden"
    
    # AI Model events
    AI_MODEL_TRAINED = "ai_model.trained"
    AI_MODEL_DEPLOYED = "ai_model.deployed"
    AI_MODEL_UPDATED = "ai_model.updated"
    AI_MODEL_DELETED = "ai_model.deleted"
    
    # Application events
    APPLICATION_CREATED = "application.created"
    APPLICATION_UPDATED = "application.updated"
    APPLICATION_STATUS_CHANGED = "application.status_changed"
    APPLICATION_DELETED = "application.deleted"
    
    # File events
    FILE_UPLOADED = "file.uploaded"
    FILE_DOWNLOADED = "file.downloaded"
    FILE_DELETED = "file.deleted"
    
    # System events
    CONFIGURATION_CHANGED = "configuration.changed"
    BACKUP_CREATED = "backup.created"
    BACKUP_RESTORED = "backup.restored"
    SYSTEM_MAINTENANCE = "system.maintenance"
    SECURITY_ALERT = "security.alert"
    
    # API events
    API_CALL = "api.call"
    API_RATE_LIMIT_EXCEEDED = "api.rate_limit_exceeded"
    API_ERROR = "api.error"
    
    # Data events
    DATA_EXPORT = "data.export"
    DATA_IMPORT = "data.import"
    DATA_DELETION = "data.deletion"
    
    # Custom events
    CUSTOM_EVENT = "custom.event"


class AuditSeverity(str, Enum):
    """Severity levels for audit events"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AuditLog(Document):
    event_type: AuditEventType = Field(..., description="Type of audit event")
    event_name: str = Field(..., description="Human-readable event name")
    description: Optional[str] = Field(None, description="Detailed description")
    severity: AuditSeverity = Field(AuditSeverity.LOW, description="Event severity")
    
    user_id: Optional[ObjectId] = Field(None, description="ID of user who performed action")
    user_email: Optional[str] = Field(None, description="Email of user")
    user_ip: Optional[str] = Field(None, description="IP address of user")
    user_agent: Optional[str] = Field(None, description="User agent string")
    session_id: Optional[str] = Field(None, description="Session identifier")
    
    resource_type: Optional[str] = Field(None, description="Type of resource affected")
    resource_id: Optional[ObjectId] = Field(None, description="ID of resource affected")
    resource_name: Optional[str] = Field(None, description="Name of resource")
    
    action: str = Field(..., description="Action performed")
    method: Optional[str] = Field(None, description="HTTP method if applicable")
    endpoint: Optional[str] = Field(None, description="API endpoint if applicable")
    request_id: Optional[str] = Field(None, description="Request identifier")
    
    old_values: Dict[str, Any] = Field(default_factory=dict, description="Values before change")
    new_values: Dict[str, Any] = Field(default_factory=dict, description="Values after change")
    changed_fields: List[str] = Field(default_factory=list, description="Fields that changed")
    
    success: bool = Field(True, description="Whether the action was successful")
    error_message: Optional[str] = Field(None, description="Error message if failed")
    error_code: Optional[str] = Field(None, description="Error code if failed")
    response_status: Optional[int] = Field(None, description="HTTP response status")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata")
    tags: List[str] = Field(default_factory=list, description="Tags for categorization")
    timestamp: datetime = Field(default_factory=lambda: now_vn(), description="When event occurred")
    duration_ms: Optional[float] = Field(None, description="Duration in milliseconds")
    created_at: datetime = Field(default_factory=lambda: now_vn(), description="When log was created")

    class Settings:
        name = "audit_logs"
        indexes = [
            {"key": [("timestamp", -1)], "name": "idx_audit_timestamp_desc"},
            {"key": [("event_type", 1)], "name": "idx_audit_event_type"},
            {"key": [("user_id", 1)], "name": "idx_audit_user_id"},
            {"key": [("user_email", 1)], "name": "idx_audit_user_email"},
            {"key": [("resource_type", 1)], "name": "idx_audit_resource_type"},
            {"key": [("resource_id", 1)], "name": "idx_audit_resource_id"},
            {"key": [("severity", 1)], "name": "idx_audit_severity"},
            {"key": [("success", 1)], "name": "idx_audit_success"},
            {"key": [("user_id", 1), ("timestamp", -1)], "name": "idx_audit_user_timestamp"},
            {"key": [("event_type", 1), ("timestamp", -1)], "name": "idx_audit_event_timestamp"},
            {"key": [("resource_type", 1), ("resource_id", 1)], "name": "idx_audit_resource"},
            {"key": [("timestamp", -1), ("severity", 1)], "name": "idx_audit_timestamp_severity"},
            {"key": [("timestamp", 1)], "expireAfterSeconds": 31536000, "name": "idx_audit_ttl"},
        ]
    class Config:
        arbitrary_types_allowed = True