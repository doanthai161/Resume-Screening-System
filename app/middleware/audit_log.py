import json
import logging
from datetime import datetime
from typing import Callable, Dict, Any, Optional, Union
from functools import wraps
from fastapi import Request, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel
from bson import ObjectId
import inspect
import time
import asyncio

from app.models.audit_log import AuditLog
from app.models.user import User
from app.utils.time import now_utc
from app.core.security import get_current_user
from app.core.config import settings

logger = logging.getLogger(__name__)


class AuditLogEntry(BaseModel):
    action: str
    resource_type: str
    resource_id: Optional[str] = None
    user_id: Optional[str] = None
    user_email: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    request_method: Optional[str] = None
    request_path: Optional[str] = None
    request_params: Optional[Dict[str, Any]] = None
    request_body: Optional[Dict[str, Any]] = None
    response_status: Optional[int] = None
    response_body: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None
    severity: str = "info"
    success: bool = True
    error_message: Optional[str] = None
    duration_ms: Optional[float] = None
    timestamp: datetime


class AuditLogConfig:
    ACTION_MAPPING = {
        "user.login": "user",
        "user.logout": "user",
        "user.create": "user",
        "user.update": "user",
        "user.delete": "user",
        "user.password_change": "user",
        "user.password_reset": "user",
        "user.verify": "user",
        
        "company.create": "company",
        "company.update": "company",
        "company.delete": "company",
        "company.member_add": "company_member",
        "company.member_remove": "company_member",
        "company.member_update": "company_member",
        
        "company_branch.create": "company_branch",
        "company_branch.update": "company_branch",
        "company_branch.delete": "company_branch",
        
        "user_company.assigned": "user_company",
        "user_company.unassigned": "user_company",
        "user_company.deleted": "user_company",
        "user_company.role_updated": "user_company",
        
        "role.create": "role",
        "role.update": "role",
        "role.delete": "role",
        "role.assign": "role_permission",
        
        "permission.create": "permission",
        "permission.update": "permission",
        "permission.delete": "permission",
        
        "document.upload": "document",
        "document.download": "document",
        "document.delete": "document",
        "document.share": "document",
        
        "settings.update": "settings",
        "config.update": "config",
        
        "api_key.create": "api_key",
        "api_key.revoke": "api_key",
        "api_key.rotate": "api_key",
    }
    
    SENSITIVE_FIELDS = {
        "password",
        "new_password",
        "current_password",
        "confirm_password",
        "hashed_password",
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "secret_key",
        "private_key",
        "credit_card",
        "cvv",
        "ssn",
        "social_security_number",
        "phone_number",
        "email",
        "address",
        "birth_date"
    }
    
    PUBLIC_ACTIONS = {
        "user.login",
        "user.register",
        "user.password_reset_request",
        "user.verify_email",
        "health.check"
    }
    
    BODY_METHODS = {"POST", "PUT", "PATCH"}
    
    @staticmethod
    def get_resource_type(action: str) -> str:
        return AuditLogConfig.ACTION_MAPPING.get(
            action, 
            action.split(".")[0] if "." in action else "unknown"
        )
    
    @staticmethod
    def mask_sensitive_data(data: Dict[str, Any]) -> Dict[str, Any]:
        if not data:
            return data
        
        masked_data = data.copy()
        
        def mask_value(value: Any) -> Any:
            if isinstance(value, str) and len(value) > 0:
                return "***MASKED***"
            return value
        
        for key in list(masked_data.keys()):
            key_lower = key.lower()
            if any(sensitive in key_lower for sensitive in AuditLogConfig.SENSITIVE_FIELDS):
                masked_data[key] = mask_value(masked_data[key])
            elif isinstance(masked_data[key], dict):
                masked_data[key] = AuditLogConfig.mask_sensitive_data(masked_data[key])
            elif isinstance(masked_data[key], list):
                masked_data[key] = [
                    AuditLogConfig.mask_sensitive_data(item) if isinstance(item, dict) else (
                        mask_value(item) if any(sensitive in str(key).lower() for sensitive in AuditLogConfig.SENSITIVE_FIELDS) else item
                    )
                    for item in masked_data[key]
                ]
        
        return masked_data


def audit_log_action(
    action: str,
    resource_id_param: Optional[str] = None,
    extract_resource_id: Optional[Callable] = None,
    include_request_body: bool = True,
    include_response_body: bool = False,
    sensitive: bool = True,
    async_mode: bool = True
):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            import time
            start_time = time.time()
            
            request = None
            for arg in args:
                if isinstance(arg, Request):
                    request = arg
                    break
            
            if request is None:
                for key, value in kwargs.items():
                    if isinstance(value, Request):
                        request = value
                        break
            
            audit_data = {
                "action": action,
                "resource_type": AuditLogConfig.get_resource_type(action),
                "success": True,
                "severity": "info",
                "timestamp": now_utc(),
                "metadata": {}
            }
            
            try:
                if request:
                    audit_data.update({
                        "ip_address": request.client.host if request.client else None,
                        "user_agent": request.headers.get("user-agent"),
                        "request_method": request.method,
                        "request_path": request.url.path,
                        "request_params": dict(request.query_params)
                    })
                
                current_user = None
                try:
                    for arg in args:
                        if isinstance(arg, User):
                            current_user = arg
                            break
                    
                    if current_user is None:
                        for key, value in kwargs.items():
                            if isinstance(value, User):
                                current_user = value
                                break
                    
                    if current_user:
                        audit_data.update({
                            "user_id": str(current_user.id),
                            "user_email": current_user.email
                        })
                    elif request and action in AuditLogConfig.PUBLIC_ACTIONS:
                        try:
                            user = await get_current_user(request)
                            if user:
                                audit_data.update({
                                    "user_id": str(user.id),
                                    "user_email": user.email
                                })
                        except Exception:
                            pass
                except Exception as e:
                    logger.debug(f"Could not extract user info: {e}")
                
                if request and include_request_body and request.method in AuditLogConfig.BODY_METHODS:
                    try:
                        body = await request.json()
                        if sensitive:
                            body = AuditLogConfig.mask_sensitive_data(body)
                        audit_data["request_body"] = body
                    except Exception:
                        try:
                            body = await request.body()
                            if body:
                                audit_data["request_body"] = {"raw": body.decode('utf-8', errors='ignore')[:1000]}
                        except Exception as e:
                            logger.debug(f"Could not extract request body: {e}")
                
                resource_id = None
                
                if resource_id_param and resource_id_param in kwargs:
                    resource_id = kwargs[resource_id_param]
                
                if extract_resource_id and not resource_id:
                    try:
                        resource_id = extract_resource_id(*args, **kwargs)
                    except Exception as e:
                        logger.debug(f"Could not extract resource ID: {e}")
                
                if not resource_id and request and hasattr(request, "path_params"):
                    resource_id = request.path_params.get(resource_id_param or "id")
                
                if resource_id:
                    audit_data["resource_id"] = str(resource_id)
                
                response = await func(*args, **kwargs)
                
                duration_ms = (time.time() - start_time) * 1000
                audit_data["duration_ms"] = round(duration_ms, 2)
                
                if isinstance(response, Response):
                    audit_data["response_status"] = response.status_code
                    
                    if include_response_body and hasattr(response, "body"):
                        try:
                            if response.body:
                                body_str = response.body.decode('utf-8', errors='ignore')
                                if body_str:
                                    body_data = json.loads(body_str)
                                    if sensitive:
                                        body_data = AuditLogConfig.mask_sensitive_data(body_data)
                                    audit_data["response_body"] = body_data
                        except Exception:
                            pass
                
                elif hasattr(response, "status_code"):
                    audit_data["response_status"] = response.status_code
                
                if audit_data.get("response_status", 200) >= 400:
                    audit_data["success"] = False
                    audit_data["severity"] = "error"
                
                return response
                
            except HTTPException as e:
                duration_ms = (time.time() - start_time) * 1000
                audit_data.update({
                    "success": False,
                    "severity": "error",
                    "response_status": e.status_code,
                    "error_message": e.detail,
                    "duration_ms": round(duration_ms, 2)
                })
                
                await _log_audit_entry(audit_data, async_mode=False)
                raise
                
            except Exception as e:
                duration_ms = (time.time() - start_time) * 1000
                audit_data.update({
                    "success": False,
                    "severity": "critical",
                    "response_status": status.HTTP_500_INTERNAL_SERVER_ERROR,
                    "error_message": str(e),
                    "duration_ms": round(duration_ms, 2)
                })
                
                await _log_audit_entry(audit_data, async_mode=False)
                raise
                
            finally:
                if audit_data.get("success", True):
                    await _log_audit_entry(audit_data, async_mode)
        
        return wrapper
    return decorator


async def _log_audit_entry(audit_data: Dict[str, Any], async_mode: bool = True):
    try:
        entry = AuditLogEntry(**audit_data)
        
        audit_log = AuditLog(
            action=entry.action,
            resource_type=entry.resource_type,
            resource_id=ObjectId(entry.resource_id) if entry.resource_id else None,
            user_id=ObjectId(entry.user_id) if entry.user_id else None,
            user_email=entry.user_email,
            ip_address=entry.ip_address,
            user_agent=entry.user_agent,
            request_method=entry.request_method,
            request_path=entry.request_path,
            request_params=entry.request_params,
            request_body=entry.request_body,
            response_status=entry.response_status,
            response_body=entry.response_body,
            metadata=entry.metadata,
            severity=entry.severity,
            success=entry.success,
            error_message=entry.error_message,
            duration_ms=entry.duration_ms,
            timestamp=entry.timestamp
        )
        
        if async_mode:
            asyncio.create_task(_save_audit_log_async(audit_log))
        else:
            await audit_log.insert()
            
    except Exception as e:
        logger.error(f"Failed to create audit log entry: {e}", exc_info=True)


async def _save_audit_log_async(audit_log: AuditLog):
    try:
        await audit_log.insert()
    except Exception as e:
        logger.error(f"Failed to save audit log asynchronously: {e}")


class AuditLogMiddleware:
    
    def __init__(self, app, exclude_paths: Optional[list] = None):
        self.app = app
        self.exclude_paths = exclude_paths or [
            "/health",
            "/metrics",
            "/docs",
            "/redoc",
            "/openapi.json",
            "/favicon.ico"
        ]
    
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        
        path = scope.get("path", "")
        if any(path.startswith(exclude) for exclude in self.exclude_paths):
            await self.app(scope, receive, send)
            return
        
        from fastapi import Request
        request = Request(scope, receive)
        
        import time
        start_time = time.time()
        
        from fastapi.responses import JSONResponse
        response = None
        
        async def send_wrapper(message):
            nonlocal response
            if message.get("type") == "http.response.start":
                response = JSONResponse(
                    content=None,
                    status_code=message.get("status", 200)
                )
            await send(message)
        
        try:
            await self.app(scope, receive, send_wrapper)
            
            await self._log_request(request, response, start_time, None)
            
        except Exception as exc:
            await self._log_request(request, response, start_time, exc)
            raise
    
    async def _log_request(self, request: Request, response: Response, start_time: float, exc: Optional[Exception]):
        try:
            import time
            duration_ms = (time.time() - start_time) * 1000
            
            user_id = None
            user_email = None
            
            try:
                user = await get_current_user(request)
                if user:
                    user_id = str(user.id)
                    user_email = user.email
            except Exception:
                pass
            
            action = f"http.{request.method.lower()}"
            
            audit_log = AuditLog(
                action=action,
                resource_type="http_request",
                request_method=request.method,
                request_path=request.url.path,
                request_params=dict(request.query_params),
                user_id=ObjectId(user_id) if user_id else None,
                user_email=user_email,
                ip_address=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent"),
                response_status=response.status_code if response else 500,
                success=exc is None,
                severity="error" if exc else "info",
                error_message=str(exc) if exc else None,
                duration_ms=round(duration_ms, 2),
                timestamp=now_utc(),
                metadata={
                    "scheme": request.url.scheme,
                    "headers": dict(request.headers),
                    "client": str(request.client) if request.client else None
                }
            )
            
            await audit_log.insert()
            
        except Exception as e:
            logger.error(f"Failed to log request: {e}")


async def log_audit_action(
    event_type: str,
    action: str,
    resource_type: str,
    event_name: Optional[str] = None,
    resource_id: Optional[str] = None,
    user_id: Optional[str] = None,
    user_email: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    request_method: Optional[str] = None,
    request_path: Optional[str] = None,
    request_params: Optional[Dict[str, Any]] = None,
    request_body: Optional[Dict[str, Any]] = None,
    response_status: Optional[int] = None,
    response_body: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    severity: str = "critical",
    success: bool = True,
    error_message: Optional[str] = None,
    duration_ms: Optional[float] = None,
    timestamp: Optional[datetime] = None,
    async_mode: bool = True
):
    try:
        audit_data = {
            "event_type": event_type,
            "event_name": event_name,
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "user_id": user_id,
            "user_email": user_email,
            "ip_address": ip_address,
            "user_agent": user_agent,
            "request_method": request_method,
            "request_path": request_path,
            "request_params": request_params,
            "request_body": request_body,
            "response_status": response_status,
            "response_body": response_body,
            "metadata": metadata or {},
            "severity": severity,
            "success": success,
            "error_message": error_message,
            "duration_ms": duration_ms,
            "timestamp": timestamp or now_utc()
        }
        
        await _log_audit_entry(audit_data, async_mode)
        
    except Exception as e:
        logger.error(f"Failed in manual audit logging: {e}")


async def log_security_event_async(
    event_type: str,
    description: str,
    user_id: Optional[str] = None,
    ip_address: Optional[str] = None,
    severity: str = "warning",
    metadata: Optional[Dict[str, Any]] = None
):
    await log_audit_action(
        event_type=event_type,
        action=f"security.{event_type}",
        resource_type="security",
        user_id=user_id,
        ip_address=ip_address,
        metadata={"description": description, **(metadata or {})},
        severity=severity,
        success=False
    )


def log_security_event(
    event_type: str,
    description: str,
    user_id: Optional[str] = None,
    ip_address: Optional[str] = None,
    severity: str = "warning",
    metadata: Optional[Dict[str, Any]] = None,
    *args,
    **kwargs
):
    
    is_direct_call = not any([
        '_background_task' in kwargs,
        '_request' in kwargs,
        len(args) > 0
    ])
    
    def wrapper():
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(
                log_security_event_async(
                    event_type=event_type,
                    description=description,
                    user_id=user_id,
                    ip_address=ip_address,
                    severity=severity,
                    metadata=metadata
                )
            )
            loop.close()
        except Exception as e:
            logger.error(f"Failed to log security event: {e}")
    
    if is_direct_call:
        wrapper()
        return None
    
    return wrapper


async def log_business_event(
    event_type: str,
    resource_type: str,
    resource_id: Optional[str] = None,
    user_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None
):
    await log_audit_action(
        event_type=event_type,
        action=f"business.{event_type}",
        resource_type=resource_type,
        resource_id=resource_id,
        user_id=user_id,
        metadata=metadata,
        severity="info",
        success=True
    )


class AuditLogContext:
    
    def __init__(
        self,
        action: str,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        user_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ):
        self.action = action
        self.resource_type = resource_type or AuditLogConfig.get_resource_type(action)
        self.resource_id = resource_id
        self.user_id = user_id
        self.metadata = metadata or {}
        self.start_time = None
        self.success = True
        self.error_message = None
    
    async def __aenter__(self):
        self.start_time = time.time()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        import time
        duration_ms = (time.time() - self.start_time) * 1000 if self.start_time else None
        
        if exc_val:
            self.success = False
            self.error_message = str(exc_val)
        
        await log_audit_action(
            action=self.action,
            resource_type=self.resource_type,
            resource_id=self.resource_id,
            user_id=self.user_id,
            metadata=self.metadata,
            success=self.success,
            error_message=self.error_message,
            duration_ms=round(duration_ms, 2) if duration_ms else None
        )
        
        return False


async def get_audit_logs(
    action: Optional[str] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    user_id: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    success: Optional[bool] = None,
    severity: Optional[str] = None,
    skip: int = 0,
    limit: int = 100
) -> list:
    try:
        query = {}
        
        if action:
            query["action"] = action
        if resource_type:
            query["resource_type"] = resource_type
        if resource_id:
            query["resource_id"] = ObjectId(resource_id)
        if user_id:
            query["user_id"] = ObjectId(user_id)
        if start_date or end_date:
            query["timestamp"] = {}
            if start_date:
                query["timestamp"]["$gte"] = start_date
            if end_date:
                query["timestamp"]["$lte"] = end_date
        if success is not None:
            query["success"] = success
        if severity:
            query["severity"] = severity
        
        logs = await AuditLog.find(query) \
            .sort([("timestamp", -1)]) \
            .skip(skip) \
            .limit(limit) \
            .to_list()
        
        return logs
        
    except Exception as e:
        logger.error(f"Failed to query audit logs: {e}")
        return []


async def get_user_activity_logs(
    user_id: str,
    days: int = 30,
    skip: int = 0,
    limit: int = 100
) -> list:
    try:
        import datetime
        end_date = now_utc()
        start_date = end_date - datetime.timedelta(days=days)
        
        logs = await AuditLog.find({
            "user_id": ObjectId(user_id),
            "timestamp": {"$gte": start_date, "$lte": end_date}
        }) \
        .sort([("timestamp", -1)]) \
        .skip(skip) \
        .limit(limit) \
        .to_list()
        
        return logs
        
    except Exception as e:
        logger.error(f"Failed to get user activity logs: {e}")
        return []


async def cleanup_old_audit_logs(days_to_keep: int = 90):
    try:
        import datetime
        cutoff_date = now_utc() - datetime.timedelta(days=days_to_keep)
        
        result = await AuditLog.find({
            "timestamp": {"$lt": cutoff_date},
            "severity": {"$ne": "critical"}
        }).delete()
        
        logger.info(f"Cleaned up {result.deleted_count} old audit logs")
        return result.deleted_count
        
    except Exception as e:
        logger.error(f"Failed to cleanup old audit logs: {e}")
        return 0