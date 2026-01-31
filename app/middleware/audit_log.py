from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
import time
import asyncio
import logging

logger = logging.getLogger(__name__)

class AuditLogMiddleware(BaseHTTPMiddleware):
    """Middleware to automatically log API calls"""
    
    async def dispatch(self, request: Request, call_next):
        # Skip non-API routes
        if not request.url.path.startswith("/api/"):
            return await call_next(request)
        
        start_time = time.time()
        
        try:
            response = await call_next(request)
            duration_ms = (time.time() - start_time) * 1000
            
            # Log API call asynchronously với error handling
            task = asyncio.create_task(
                self.log_api_call(
                    request=request,
                    response=response,
                    duration_ms=duration_ms
                )
            )
            
            # Thêm error handling cho task
            task.add_done_callback(self._handle_audit_log_error)
            
            return response
            
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            
            # Log failed API call với error handling
            task = asyncio.create_task(
                self.log_security_event(
                    request=request,
                    error=e,
                    duration_ms=duration_ms
                )
            )
            task.add_done_callback(self._handle_audit_log_error)
            
            raise
    
    @staticmethod
    def _handle_audit_log_error(task):
        """Handle errors from async audit log tasks"""
        try:
            task.result()
        except Exception as e:
            logger.error(f"Audit log task failed: {e}", exc_info=True)
    
    @staticmethod
    async def log_api_call(
        request: Request,
        response: Response,
        duration_ms: float
    ):
        """Helper method to log API calls"""
        try:
            # Import inside function để tránh circular imports
            from app.schemas.audit_log import AuditLogCreate
            from app.models.audit_log import AuditEventType, AuditSeverity
            from app.services.audit_log_service import AuditLogService
            
            # Chỉ log các response có status code
            if not hasattr(response, 'status_code'):
                return
            
            log_data = AuditLogCreate(
                event_type=AuditEventType.API_CALL,
                event_name=f"API Call: {request.method} {request.url.path}",
                description=f"API endpoint called: {request.url.path}",
                severity=AuditSeverity.LOW,
                user_ip=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent"),
                method=request.method,
                endpoint=str(request.url.path),
                request_id=request.headers.get("x-request-id"),
                action="api_call",
                success=response.status_code < 400,
                response_status=response.status_code,
                duration_ms=duration_ms,
                metadata={
                    "query_params": dict(request.query_params),
                    "path_params": dict(request.path_params),
                },
                tags=["api"]
            )
            
            await AuditLogService.create_log(log_data)
            
        except ImportError as e:
            logger.warning(f"Audit logging not available: {e}")
        except Exception as e:
            logger.error(f"Failed to log API call: {e}", exc_info=True)
    
    @staticmethod
    async def log_security_event(
        request: Request,
        error: Exception,
        duration_ms: float
    ):
        """Log security-related events"""
        try:
            from app.models.audit_log import AuditEventType, AuditSeverity
            from app.services.audit_log_service import AuditLogService
            
            await AuditLogService.log_security_event(
                event_type=AuditEventType.API_ERROR,
                user_ip=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent"),
                details={
                    "path": request.url.path,
                    "method": request.method,
                    "error": str(error),
                    "duration_ms": duration_ms
                },
                success=False,
                error_message=str(error)
            )
            
        except ImportError as e:
            logger.warning(f"Security event logging not available: {e}")
        except Exception as e:
            logger.error(f"Failed to log security event: {e}", exc_info=True)