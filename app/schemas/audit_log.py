from typing import Optional, Dict, Any, List
from datetime import datetime
from pydantic import BaseModel, Field
from app.models.audit_log import AuditEventType, AuditSeverity


class AuditLogCreate(BaseModel):
    """Schema for creating audit log entries (API input)"""
    event_type: AuditEventType
    event_name: str
    description: Optional[str] = None
    severity: AuditSeverity = AuditSeverity.LOW
    
    # Use string for IDs in API schemas
    user_id: Optional[str] = Field(None, description="ID of user who performed action")
    user_email: Optional[str] = Field(None, description="Email of user")
    user_ip: Optional[str] = Field(None, description="IP address of user")
    user_agent: Optional[str] = Field(None, description="User agent string")
    session_id: Optional[str] = Field(None, description="Session identifier")
    
    resource_type: Optional[str] = Field(None, description="Type of resource affected")
    resource_id: Optional[str] = Field(None, description="ID of resource affected")
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
    
    duration_ms: Optional[float] = Field(None, description="Duration in milliseconds")


class AuditLogResponse(BaseModel):
    """Schema for audit log responses (API output)"""
    id: str
    event_type: AuditEventType
    event_name: str
    description: Optional[str] = None
    severity: AuditSeverity
    
    user_id: Optional[str] = None
    user_email: Optional[str] = None
    user_ip: Optional[str] = None
    user_agent: Optional[str] = None
    session_id: Optional[str] = None
    
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    resource_name: Optional[str] = None
    
    action: str
    method: Optional[str] = None
    endpoint: Optional[str] = None
    request_id: Optional[str] = None
    
    old_values: Dict[str, Any]
    new_values: Dict[str, Any]
    changed_fields: List[str]
    
    success: bool
    error_message: Optional[str] = None
    error_code: Optional[str] = None
    response_status: Optional[int] = None
    
    metadata: Dict[str, Any]
    tags: List[str]
    
    timestamp: datetime
    duration_ms: Optional[float] = None
    created_at: datetime


class AuditLogQuery(BaseModel):
    """Query parameters for searching audit logs"""
    event_type: Optional[AuditEventType] = None
    user_id: Optional[str] = None
    user_email: Optional[str] = None
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    severity: Optional[AuditSeverity] = None
    success: Optional[bool] = None
    
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    
    search_text: Optional[str] = None
    tags: Optional[List[str]] = None
    
    page: int = 1
    limit: int = 100
    sort_by: str = "timestamp"
    sort_order: str = "desc"


class AuditLogSummary(BaseModel):
    """Summary statistics for audit logs"""
    total_events: int = 0
    successful_events: int = 0
    failed_events: int = 0
    
    events_by_type: Dict[str, int] = {}
    events_by_severity: Dict[str, int] = {}
    events_by_user: Dict[str, int] = {}
    events_by_resource: Dict[str, int] = {}
    
    average_duration_ms: Optional[float] = None
    peak_hour: Optional[int] = None
    busiest_day: Optional[str] = None


class AuditLogListResponse(BaseModel):
    """Response for paginated audit log list"""
    logs: List[AuditLogResponse]
    pagination: Dict[str, Any]