from typing import Optional, Dict, Any
from datetime import datetime
from bson import ObjectId
from app.models.audit_log import AuditLog, AuditEventType, AuditSeverity
from app.schemas.audit_log import (
    AuditLogCreate, 
    AuditLogQuery, 
    AuditLogResponse,
    AuditLogSummary,
    AuditLogListResponse
)


class AuditLogService:
    
    @staticmethod
    def _convert_to_response(log: AuditLog) -> AuditLogResponse:
        """Convert database model to response schema"""
        data = log.dict()
        data["id"] = str(log.id)
        
        # Convert ObjectId to string
        if data.get("user_id"):
            data["user_id"] = str(data["user_id"])
        if data.get("resource_id"):
            data["resource_id"] = str(data["resource_id"])
        
        return AuditLogResponse(**data)
    
    @staticmethod
    async def create_log(log_data: AuditLogCreate) -> AuditLog:
        """Create an audit log entry"""
        try:
            # Convert string IDs to ObjectId for database
            data = log_data.dict()
            if data.get("user_id"):
                data["user_id"] = ObjectId(data["user_id"])
            if data.get("resource_id"):
                data["resource_id"] = ObjectId(data["resource_id"])
            
            audit_log = AuditLog(**data)
            await audit_log.insert()
            return audit_log
        except Exception as e:
            import logging
            logging.error(f"Failed to write audit log: {e}, Data: {log_data}")
            raise
    
    @staticmethod
    async def log_security_event(
        event_type: AuditEventType,
        user_id: Optional[str] = None,
        user_email: Optional[str] = None,
        user_ip: Optional[str] = None,
        user_agent: Optional[str] = None,
        details: Optional[Dict] = None,
        success: bool = True,
        error_message: Optional[str] = None
    ):
        """Log security-related events"""
        log_data = AuditLogCreate(
            event_type=event_type,
            event_name=event_type.value.replace(".", " ").title(),
            description=f"Security event: {event_type.value}",
            severity=AuditSeverity.HIGH if not success else AuditSeverity.MEDIUM,
            user_id=user_id,
            user_email=user_email,
            user_ip=user_ip,
            user_agent=user_agent,
            action="security",
            success=success,
            error_message=error_message,
            metadata=details or {},
            tags=["security"]
        )
        
        await AuditLogService.create_log(log_data)
    
    @staticmethod
    async def search_logs(query: AuditLogQuery) -> AuditLogListResponse:
        """Search audit logs with pagination"""
        from pymongo import DESCENDING, ASCENDING
        
        filters = {}
        
        # Convert string IDs to ObjectId for query
        if query.user_id:
            try:
                filters["user_id"] = ObjectId(query.user_id)
            except:
                pass  # Invalid ObjectId format
        
        if query.resource_id:
            try:
                filters["resource_id"] = ObjectId(query.resource_id)
            except:
                pass  # Invalid ObjectId format
        
        # Build other filters
        if query.event_type:
            filters["event_type"] = query.event_type
        if query.user_email:
            filters["user_email"] = {"$regex": query.user_email, "$options": "i"}
        if query.resource_type:
            filters["resource_type"] = query.resource_type
        if query.severity:
            filters["severity"] = query.severity
        if query.success is not None:
            filters["success"] = query.success
        
        # Date range filter
        if query.start_date or query.end_date:
            filters["timestamp"] = {}
            if query.start_date:
                filters["timestamp"]["$gte"] = query.start_date
            if query.end_date:
                filters["timestamp"]["$lte"] = query.end_date
        
        # Text search
        if query.search_text:
            filters["$or"] = [
                {"event_name": {"$regex": query.search_text, "$options": "i"}},
                {"description": {"$regex": query.search_text, "$options": "i"}},
                {"user_email": {"$regex": query.search_text, "$options": "i"}},
            ]
        
        # Tags filter
        if query.tags:
            filters["tags"] = {"$all": query.tags}
        
        # Calculate skip
        skip = (query.page - 1) * query.limit
        
        # Sort
        sort_order = DESCENDING if query.sort_order == "desc" else ASCENDING
        
        # Query
        cursor = AuditLog.find(filters)
        total = await cursor.count()
        
        logs = await cursor.sort([(query.sort_by, sort_order)]) \
                          .skip(skip) \
                          .limit(query.limit) \
                          .to_list()
        
        # Convert to response schema
        response_logs = [AuditLogService._convert_to_response(log) for log in logs]
        
        return AuditLogListResponse(
            logs=response_logs,
            pagination={
                "total": total,
                "page": query.page,
                "limit": query.limit,
                "pages": (total + query.limit - 1) // query.limit
            }
        )
    
    @staticmethod
    async def get_summary(
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None
    ) -> AuditLogSummary:
        """Get audit log summary statistics"""
        filters = {}
        if start_date or end_date:
            filters["timestamp"] = {}
            if start_date:
                filters["timestamp"]["$gte"] = start_date
            if end_date:
                filters["timestamp"]["$lte"] = end_date
        
        # Get all logs in date range
        logs = await AuditLog.find(filters).to_list()
        
        if not logs:
            return AuditLogSummary()
        
        summary = AuditLogSummary(
            total_events=len(logs),
            successful_events=sum(1 for log in logs if log.success),
            failed_events=sum(1 for log in logs if not log.success)
        )
        
        # Group by event type
        for log in logs:
            event_type = log.event_type.value
            summary.events_by_type[event_type] = summary.events_by_type.get(event_type, 0) + 1
            
            # Group by severity
            severity = log.severity.value
            summary.events_by_severity[severity] = summary.events_by_severity.get(severity, 0) + 1
            
            # Group by user
            if log.user_email:
                summary.events_by_user[log.user_email] = summary.events_by_user.get(log.user_email, 0) + 1
            
            # Group by resource
            if log.resource_type:
                key = f"{log.resource_type}:{log.resource_name or 'unknown'}"
                summary.events_by_resource[key] = summary.events_by_resource.get(key, 0) + 1
        
        # Calculate average duration
        durations = [log.duration_ms for log in logs if log.duration_ms]
        if durations:
            summary.average_duration_ms = sum(durations) / len(durations)
        
        from collections import Counter
        hours = [log.timestamp.hour for log in logs]
        if hours:
            hour_counts = Counter(hours)
            summary.peak_hour = hour_counts.most_common(1)[0][0]
        
        days = [log.timestamp.strftime("%Y-%m-%d") for log in logs]
        if days:
            day_counts = Counter(days)
            summary.busiest_day = day_counts.most_common(1)[0][0]
        
        return summary