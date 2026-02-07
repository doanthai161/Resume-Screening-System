# app/core/security.py
import os
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional, Set, Dict, Any, Union
from functools import lru_cache
import logging

from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer, OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from redis.asyncio import Redis

from app.core.config import settings
from app.models.user import User
from app.models.user_actor import UserActor
from app.models.permission import Permission
from app.models.actor import Actor
from app.models.actor_permission import ActorPermission
from app.core.redis import get_redis

logger = logging.getLogger(__name__)

# ==================== SECURITY CONFIGURATION ====================
@lru_cache()
def get_password_context() -> CryptContext:
    """Get password hashing context with Argon2"""
    return CryptContext(
        schemes=["argon2"],
        argon2__time_cost=2,
        argon2__memory_cost=102400,
        argon2__parallelism=8,
        deprecated="auto"
    )

@lru_cache()
def get_jwt_settings() -> Dict[str, Any]:
    """Get JWT settings"""
    return {
        "algorithm": settings.ALGORITHM,
        "secret_key": settings.SECRET_KEY.get_secret_value() if hasattr(settings.SECRET_KEY, 'get_secret_value') else settings.SECRET_KEY,
        "access_token_expire_minutes": settings.ACCESS_TOKEN_EXPIRE_MINUTES,
        "refresh_token_expire_days": settings.REFRESH_TOKEN_EXPIRE_DAYS,
    }

# Security schemes
security_bearer = HTTPBearer(
    scheme_name="JWT",
    description="Enter JWT token as: Bearer <token>",
    auto_error=False
)

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl=f"{settings.API_V1_STR}/auth/login",
    scheme_name="OAuth2",
    auto_error=False
)

# ==================== TOKEN MANAGEMENT ====================
class TokenPayload:
    """Token payload structure"""
    def __init__(self, **kwargs):
        self.sub: Optional[str] = kwargs.get("sub")
        self.email: Optional[str] = kwargs.get("email")
        self.user_id: Optional[str] = kwargs.get("user_id")
        self.scopes: List[str] = kwargs.get("scopes", [])
        self.jti: Optional[str] = kwargs.get("jti")
        self.iat: Optional[datetime] = kwargs.get("iat")
        self.exp: Optional[datetime] = kwargs.get("exp")
        self.type: Optional[str] = kwargs.get("type", "access")

class TokenPair:
    """Access and refresh token pair"""
    def __init__(self, access_token: str, refresh_token: str):
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.token_type = "bearer"

def create_access_token(
    data: Dict[str, Any],
    expires_delta: Optional[timedelta] = None,
    token_type: str = "access"
) -> str:
    """Create JWT token"""
    jwt_settings = get_jwt_settings()
    to_encode = data.copy()
    
    # Set expiration
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    elif token_type == "refresh":
        expire = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    
    # Add standard claims
    to_encode.update({
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "type": token_type,
    })
    
    # Add jti (JWT ID) for token invalidation
    import uuid
    to_encode["jti"] = str(uuid.uuid4())
    
    return jwt.encode(
        to_encode,
        jwt_settings["secret_key"],
        algorithm=jwt_settings["algorithm"]
    )

def create_token_pair(user: User, scopes: List[str] = None) -> TokenPair:
    """Create access and refresh token pair"""
    data = {
        "sub": user.email,
        "email": user.email,
        "user_id": str(user.id),
        "scopes": scopes or [],
    }
    
    access_token = create_access_token(data, token_type="access")
    refresh_token = create_access_token(data, token_type="refresh")
    
    return TokenPair(access_token=access_token, refresh_token=refresh_token)

def decode_jwt_token(token: str) -> Optional[TokenPayload]:
    """Decode and validate JWT token"""
    jwt_settings = get_jwt_settings()
    
    try:
        payload = jwt.decode(
            token,
            jwt_settings["secret_key"],
            algorithms=[jwt_settings["algorithm"]]
        )
        return TokenPayload(**payload)
    except JWTError as e:
        logger.debug(f"JWT decode error: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected token decode error: {e}")
        return None

# ==================== PASSWORD MANAGEMENT ====================
def get_password_hash(password: str) -> str:
    """Hash password using Argon2"""
    return get_password_context().hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify password against hash"""
    try:
        return get_password_context().verify(plain_password, hashed_password)
    except Exception as e:
        logger.error(f"Password verification error: {e}")
        return False

def password_strength_check(password: str) -> Dict[str, Any]:
    """Check password strength"""
    issues = []
    
    if len(password) < settings.PASSWORD_MIN_LENGTH:
        issues.append(f"Password must be at least {settings.PASSWORD_MIN_LENGTH} characters")
    
    if len(password) > settings.PASSWORD_MAX_LENGTH:
        issues.append(f"Password must be at most {settings.PASSWORD_MAX_LENGTH} characters")
    
    import re
    if not re.search(r"[A-Z]", password):
        issues.append("Password must contain at least one uppercase letter")
    if not re.search(r"[a-z]", password):
        issues.append("Password must contain at least one lowercase letter")
    # if not re.search(r"\d", password):
    #     issues.append("Password must contain at least one digit")
    # if not re.search(r"[!@#$%^&*(),.?\":{}|<>]", password):
    #     issues.append("Password must contain at least one special character")
    
    return {
        "is_valid": len(issues) == 0,
        "issues": issues,
        "score": max(0, 100 - len(issues) * 20)  # Simple score calculation
    }

# ==================== TOKEN BLACKLIST (Redis) ====================
async def is_token_blacklisted(token: str, redis: Optional[Redis] = None) -> bool:
    """Check if token is blacklisted using Redis"""
    try:
        if not redis:
            redis = get_redis()
        
        if not redis:
            # Fallback to in-memory set if Redis is not available
            from app.core.security import _in_memory_blacklist
            return token in _in_memory_blacklist
        
        token_key = f"blacklist:token:{token}"
        return await redis.exists(token_key) > 0
    except Exception as e:
        logger.error(f"Error checking token blacklist: {e}")
        return False

async def blacklist_token(
    token: str, 
    expires_in: Optional[int] = None,
    redis: Optional[Redis] = None
):
    """Add token to blacklist with expiration"""
    try:
        if not redis:
            redis = get_redis()
        
        if not redis:
            # Fallback to in-memory set
            from app.core.security import _in_memory_blacklist
            _in_memory_blacklist.add(token)
            return
        
        token_key = f"blacklist:token:{token}"
        expire_seconds = expires_in or settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
        
        await redis.setex(token_key, expire_seconds, "1")
        logger.debug(f"Token blacklisted: {token_key}")
    except Exception as e:
        logger.error(f"Error blacklisting token: {e}")

async def blacklist_token_by_jti(jti: str, expires_in: Optional[int] = None, redis: Optional[Redis] = None):
    """Blacklist token by JWT ID"""
    try:
        if not redis:
            redis = get_redis()
        
        jti_key = f"blacklist:jti:{jti}"
        expire_seconds = expires_in or settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
        
        await redis.setex(jti_key, expire_seconds, "1")
        logger.debug(f"Token JTI blacklisted: {jti_key}")
    except Exception as e:
        logger.error(f"Error blacklisting token JTI: {e}")

class CurrentUser:    
    def __init__(
        self, 
        user: User, 
        actors: List[Actor], 
        permissions: List[Permission],
        token_payload: Optional[TokenPayload] = None
    ):
        self.user = user
        self.actors = actors
        self.permissions = permissions
        self.token_payload = token_payload
        self._permission_names = {perm.name for perm in permissions}
        self._actor_names = {actor.name for actor in actors}
        self._scopes = set(token_payload.scopes if token_payload else [])
    
    def __getattr__(self, item):
        """Delegate attribute access to user object"""
        return getattr(self.user, item)
    
    @property
    def email(self) -> str:
        return self.user.email

    @property
    def user_id(self) -> Optional[str]:
        """Get user ID as string"""
        return str(self.user.id) if self.user.id else None
    
    @property
    def is_admin(self) -> bool:
        """Check if user has admin role"""
        return settings.ADMIN_ROLE_NAME in self._actor_names
    
    @property
    def is_recruiter(self) -> bool:
        """Check if user has recruiter role"""
        return settings.RECRUITER_ROLE_NAME in self._actor_names
    
    @property
    def is_candidate(self) -> bool:
        """Check if user has candidate role"""
        return settings.CANDIDATE_ROLE_NAME in self._actor_names
    
    @property
    def is_superuser(self) -> bool:
        return self.user.is_superuser if hasattr(self.user, 'is_superuser') else False
    
    def has_permission(self, permission: str) -> bool:
        """Check if user has specific permission"""
        return permission in self._permission_names
    
    def has_any_permission(self, *permissions: str) -> bool:
        """Check if user has any of the given permissions"""
        return any(perm in self._permission_names for perm in permissions)
    
    def has_all_permissions(self, *permissions: str) -> bool:
        """Check if user has all of the given permissions"""
        return all(perm in self._permission_names for perm in permissions)
    
    def has_scope(self, scope: str) -> bool:
        """Check if user has specific scope"""
        return scope in self._scopes
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses"""
        return {
            "user_id": self.user_id,
            "email": self.user.email,
            "full_name": self.user.full_name,
            "is_active": self.user.is_active,
            "is_superuser": self.is_superuser,
            "roles": list(self._actor_names),
            "permissions": list(self._permission_names),
            "scopes": list(self._scopes),
        }

async def get_token_from_request(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_bearer),
    token: Optional[str] = Depends(oauth2_scheme)
) -> Optional[str]:
    """
    Extract token from request using multiple methods
    Priority: Bearer token > OAuth2 token > API Key
    """
    # 1. Try Bearer token
    if credentials:
        return credentials.credentials
    
    # 2. Try OAuth2 token
    if token:
        return token
    
    # 3. Try API Key header
    api_key = request.headers.get(settings.API_KEY_HEADER)
    if api_key:
        return api_key
    
    # 4. Try query parameter
    api_key = request.query_params.get("api_key")
    if api_key:
        return api_key
    
    return None

async def get_current_user(
    request: Request,
    token: Optional[str] = Depends(get_token_from_request)
) -> CurrentUser:
    from app.dependencies.error_code import ErrorCode
    
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ErrorCode.INVALID_CREDENTIALS,
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if await is_token_blacklisted(token):
        logger.warning(f"Blacklisted token attempt: {token[:20]}...")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ErrorCode.TOKEN_EXPIRED,
        )
    
    token_payload = decode_jwt_token(token)
    if not token_payload or not token_payload.sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ErrorCode.INVALID_CREDENTIALS,
        )
    
    if token_payload.exp and datetime.fromtimestamp(token_payload.exp, tz=timezone.utc) < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ErrorCode.TOKEN_EXPIRED,
        )
    
    user = await User.find_one(User.email == token_payload.email, User.is_active == True)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ErrorCode.USER_NOT_FOUND,
        )
    
    actor_links = await UserActor.find(
        UserActor.user_id == user.id
    ).to_list()
    
    actor_ids = list({link.actor_id for link in actor_links})
    actors = []
    if actor_ids:
        actors = await Actor.find(
            {"_id": {"$in": actor_ids}, "is_active": True}
        ).to_list()
    
    active_actor_ids = [actor.id for actor in actors]
    permissions = []
    if active_actor_ids:
        perm_links = await ActorPermission.find(
            {"actor_id": {"$in": active_actor_ids}}
        ).to_list()
        permission_ids = list({link.permission_id for link in perm_links})
        if permission_ids:
            permissions = await Permission.find(
                {"_id": {"$in": permission_ids}, "is_active": True}
            ).to_list()
    
    logger.info(f"User authenticated: {user.email}, roles: {[a.name for a in actors]}")
    
    return CurrentUser(
        user=user,
        actors=actors,
        permissions=permissions,
        token_payload=token_payload
    )

async def get_current_active_user(
    current_user: CurrentUser = Depends(get_current_user)
) -> CurrentUser:
    """Get current active user (additional check for active status)"""
    from app.dependencies.error_code import ErrorCode
    if not current_user.user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ErrorCode.USER_INACTIVE,
        )
    return current_user

# ==================== PERMISSION AND ROLE DEPENDENCIES ====================
def require_permission(permission: str) -> Callable:
    """Dependency to require specific permission"""
    async def permission_dependency(
        current_user: CurrentUser = Depends(get_current_active_user)
    ) -> CurrentUser:
        from app.dependencies.error_code import ErrorCode
        
        if not current_user.has_permission(permission):
            logger.warning(
                f"Permission denied for user {current_user.email}. "
                f"Required: {permission}, Has: {current_user._permission_names}"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=ErrorCode.FORBIDDEN,
            )
        return current_user
    
    return permission_dependency

def require_any_permission(*permissions: str) -> Callable:
    """Dependency to require any of the given permissions"""
    async def any_permission_dependency(
        current_user: CurrentUser = Depends(get_current_active_user)
    ) -> CurrentUser:
        from app.dependencies.error_code import ErrorCode
        
        if not current_user.has_any_permission(*permissions):
            logger.warning(
                f"Any permission denied for user {current_user.email}. "
                f"Required any of: {permissions}, Has: {current_user._permission_names}"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=ErrorCode.FORBIDDEN,
            )
        return current_user
    
    return any_permission_dependency

def require_all_permissions(*permissions: str) -> Callable:
    """Dependency to require all of the given permissions"""
    async def all_permission_dependency(
        current_user: CurrentUser = Depends(get_current_active_user)
    ) -> CurrentUser:
        from app.dependencies.error_code import ErrorCode
        
        if not current_user.has_all_permissions(*permissions):
            logger.warning(
                f"All permissions denied for user {current_user.email}. "
                f"Required all of: {permissions}, Has: {current_user._permission_names}"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=ErrorCode.FORBIDDEN,
            )
        return current_user
    
    return all_permission_dependency

def require_role(role_name: str) -> Callable:
    """Dependency to require specific role"""
    async def role_dependency(
        current_user: CurrentUser = Depends(get_current_active_user)
    ) -> CurrentUser:
        from app.dependencies.error_code import ErrorCode
        
        if role_name not in current_user._actor_names:
            logger.warning(
                f"Role denied for user {current_user.email}. "
                f"Required role: {role_name}, Has roles: {current_user._actor_names}"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=ErrorCode.FORBIDDEN,
            )
        return current_user
    
    return role_dependency

def require_admin() -> Callable:
    """Dependency to require admin role"""
    return require_role(settings.ADMIN_ROLE_NAME)

def require_recruiter() -> Callable:
    """Dependency to require recruiter role"""
    return require_role(settings.RECRUITER_ROLE_NAME)

def require_candidate() -> Callable:
    """Dependency to require candidate role"""
    return require_role(settings.CANDIDATE_ROLE_NAME)

# ==================== API KEY AUTHENTICATION ====================
async def validate_api_key(api_key: str) -> Optional[User]:
    """Validate API key and return associated user"""
    # Implement API key validation logic
    # This could check against a database of API keys
    # For now, return None (to be implemented)
    return None

async def get_current_api_user(
    request: Request,
    api_key: Optional[str] = Depends(get_token_from_request)
) -> Optional[User]:
    """Get current user via API key"""
    if not api_key:
        return None
    
    user = await validate_api_key(api_key)
    if not user or not user.is_active:
        return None
    
    return user

# ==================== RATE LIMITING HELPERS ====================
def get_client_identifier(request: Request) -> str:
    """Get unique identifier for rate limiting"""
    # Try to get user ID if authenticated
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:]
        payload = decode_jwt_token(token)
        if payload and payload.user_id:
            return f"user:{payload.user_id}"
    
    # Fallback to IP address
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        ip = forwarded.split(",")[0]
    else:
        ip = request.client.host if request.client else "unknown"
    
    return f"ip:{ip}"

# ==================== AUDIT LOGGING ====================
async def log_security_event(
    event_type: str,
    user_id: Optional[str] = None,
    email: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    details: Optional[Dict] = None,
    success: bool = True
):
    """Log security event for audit trail"""
    from app.models.audit_log import AuditLogService, AuditEventType
    from bson import ObjectId
    
    try:
        try:
            audit_event_type = AuditEventType(event_type)
        except ValueError:
            audit_event_type = AuditEventType.CUSTOM_EVENT
        
        await AuditLogService.log_security_event(
            event_type=audit_event_type,
            user_id=ObjectId(user_id) if user_id else None,
            user_email=email,
            user_ip=ip_address,
            user_agent=user_agent,
            details=details or {},
            success=success
        )
    except Exception as e:
        logger.error(f"Failed to log security event: {e}")

# In-memory fallback for blacklist (when Redis is not available)
_in_memory_blacklist: Set[str] = set()