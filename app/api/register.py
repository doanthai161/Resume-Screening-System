from fastapi import APIRouter, Depends, HTTPException, status, FastAPI, BackgroundTasks, Request
from app.schemas.user import (
    AccessToken,
    LoginRequest,
    RegisterRequest,
    UserResponse,
    VerifyOTPResponse,
    VerifyOTPRegisterRequest,
)
from app.models.user import User
from app.core.security import (
    blacklist_token,
    create_access_token,
    get_password_hash,
    get_current_user,
    verify_password,
    CurrentUser,
    require_permission,
    decode_jwt_token,
    password_strength_check,
    create_token_pair
)
from app.logs.logging_config import logger
from app.dependencies.error_code import ErrorCode
from typing import Optional, Dict
from app.schemas.email_otp import RequestOTPRequest
from app.core.email_otp import send_otp_email
from app.models.email_otp import EmailOTP
from app.utils.otp import generate_otp
from app.core.rate_limiter import limiter
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler
from app.utils.time import now_vn
from datetime import datetime, timedelta, timezone
from app.models.actor import Actor
from app.models.user_actor import UserActor
from app.core.config import settings
from bson import ObjectId
from app.repositories.user_repository import UserRepository
from app.middleware.audit_log import log_security_event, log_audit_action

router = APIRouter()

app = FastAPI()

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

@router.post("/register", status_code=status.HTTP_201_CREATED, response_model=UserResponse)
@limiter.limit("3/minute")
async def register(
    data: RegisterRequest,
    request: Request,
    background_tasks: BackgroundTasks
):
    try:
        password_check = password_strength_check(data.password)
        if not password_check["is_valid"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "message": "Password does not meet requirements",
                    "issues": password_check["issues"]
                }
            )
        
        existing_user = await UserRepository.get_user_by_email(data.email)
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ErrorCode.EMAIL_ALREADY_REGISTERED,
            )
        
        if data.phone_number:
            existing_phone_user = await User.find_one({"phone_number": data.phone_number})
            if existing_phone_user:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=ErrorCode.PHONE_ALREADY_REGISTERED,
                )
        
        user_data = {
            "email": data.email,
            "full_name": data.full_name,
            "password": data.password,
            "phone_number": data.phone_number,
            "address": data.address,
            "is_active": False,
            "is_verified": False,
        }
        
        user = await UserRepository.create_user(user_data)
        
        default_actor = await Actor.find_one(Actor.name == settings.CANDIDATE_ROLE_NAME)
        if not default_actor:
            logger.error(f"Default actor '{settings.CANDIDATE_ROLE_NAME}' not found.")
            background_tasks.add_task(
                logger.error, 
                f"Default actor '{settings.CANDIDATE_ROLE_NAME}' not found. User {data.email} registered without role assignment."
            )
        else:
            try:
                user_actor = UserActor(
                    user_id=ObjectId(user.id),
                    actor_id=ObjectId(default_actor.id),
                    created_by=ObjectId(user.id),
                    created_at=now_vn()
                )
                await user_actor.insert()
                background_tasks.add_task(
                    logger.info, 
                    f"Assigned default actor '{settings.CANDIDATE_ROLE_NAME}' to user '{data.email}'."
                )
            except Exception as e:
                logger.error(f"Failed to assign default role to user {data.email}: {e}")
                background_tasks.add_task(
                    logger.error, 
                    f"Failed to assign default role to user {data.email}: {e}"
                )
        otp_code = generate_otp()
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.OTP_EXPIRY_MINUTES)
        
        existing_otp = await EmailOTP.find_one({
            "email": data.email,
            "otp_type": "registration",
            "is_used": False
        })
        
        if existing_otp:
            existing_otp.otp_code = otp_code
            existing_otp.expires_at = expires_at
            existing_otp.attempts = 0
            existing_otp.is_used = False
            existing_otp.updated_at = now_vn()
            await existing_otp.save()
        else:
            email_otp = EmailOTP(
                email=data.email,
                otp_code=otp_code,
                otp_type="registration",
                expires_at=expires_at,
                created_at=now_vn(),
                updated_at=now_vn()
            )
            await email_otp.insert()
        
        background_tasks.add_task(
            send_otp_email,
            email=data.email,
            otp=otp_code,
            otp_type="registration",
            full_name=data.full_name
        )
        
        background_tasks.add_task(
            logger.info,
            f"User registered: {data.email}. OTP sent."
        )
        
        background_tasks.add_task(
            log_security_event,
            event_type="user_registered",
            description=f"User registered via email",
            user_id=str(user.id),
            ip_address=request.client.host if request.client else None,
            severity="info",
            metadata={
                "email": data.email,
                "registration_method": "email",
                "has_phone": bool(data.phone_number),
                "user_agent": request.headers.get("user-agent")
            }
        )
        
        return UserResponse(
            id=str(user.id),
            email=user.email,
            full_name=user.full_name,
            phone_number=user.phone_number,
            address=user.address,
            is_active=user.is_active,
            is_verified=user.is_verified,
            created_at=user.created_at,
            message="Registration successful. Please check your email for OTP verification."
        )
        
    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"Registration validation error for {data.email}: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Registration error for {data.email}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Registration failed. Please try again later."
        )

@router.post("/verify-otp", response_model=VerifyOTPResponse)
@limiter.limit("5/minute")
async def verify_otp(
    data: VerifyOTPRegisterRequest,
    request: Request,
    background_tasks: BackgroundTasks
):
    try:
        otp_record = await EmailOTP.find_one({
            "email": data.email,
            "otp_type": "registration",
            "is_used": False
        })
        
        
        if not otp_record:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ErrorCode.OTP_NOT_FOUND,
            )
        
        if otp_record.is_used:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ErrorCode.OTP_ALREADY_USED,
            )
        
        if otp_record.is_expired:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ErrorCode.OTP_EXPIRED,
            )
        
        if not otp_record.can_attempt:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ErrorCode.OTP_MAX_ATTEMPTS,
            )
        
        if otp_record.otp_code != data.otp:
            otp_record.increment_attempt()
            await otp_record.save()
            
            remaining_attempts = otp_record.max_attempts - otp_record.attempts
            
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "message": ErrorCode.INVALID_OTP,
                    "remaining_attempts": remaining_attempts
                }
            )
        
        otp_record.mark_as_used()
        await otp_record.save()
        
        user = await UserRepository.get_user_by_email(data.email)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=ErrorCode.USER_NOT_FOUND,
            )
        
        success = await UserRepository.verify_user(str(user.id))
        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to verify user"
            )
        
        token_pair = create_access_token({
            "sub": user.email,
            "email": user.email,
            "user_id": str(user.id),
            "scopes": [],
        })
        
        background_tasks.add_task(
            log_security_event,
            event_type="email_verified",
            user_id=str(user.id),
            email=data.email,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            details={"verification_method": "otp"},
            success=True
        )
        
        background_tasks.add_task(
            logger.info,
            f"User email verified: {data.email}"
        )
        
        return VerifyOTPResponse(
            success=True,
            message="Email verified successfully",
            access_token=token_pair.access_token if hasattr(token_pair, 'access_token') else token_pair,
            token_type="bearer",
            user=UserResponse(
                id=str(user.id),
                email=user.email,
                full_name=user.full_name,
                phone_number=user.phone_number,
                address=user.address,
                is_active=True,
                is_verified=True,
                created_at=user.created_at
            )
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"OTP verification error for {data.email}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="OTP verification failed"
        )

@router.post("/resend-otp", status_code=status.HTTP_200_OK)
@limiter.limit("3/minute")
async def resend_otp(
    data: RequestOTPRequest,
    request: Request,
    background_tasks: BackgroundTasks
):
    try:
        user = await UserRepository.get_user_by_email(data.email)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=ErrorCode.USER_NOT_FOUND,
            )
        
        if user.is_verified:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User already verified"
            )
        
        # Generate new OTP
        otp_code = generate_otp()
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.OTP_EXPIRY_MINUTES)
        
        # Find existing OTP
        existing_otp = await EmailOTP.find_one(
            EmailOTP.email == data.email,
            EmailOTP.otp_type == "registration",
            EmailOTP.is_used == False
        )
        
        if existing_otp:
            # Check if we should wait before resending
            time_since_creation = datetime.now(timezone.utc) - existing_otp.created_at
            if time_since_creation < timedelta(seconds=30):  # 30 seconds cooldown
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Please wait before requesting another OTP"
                )
            
            # Update existing OTP
            existing_otp.otp_code = otp_code
            existing_otp.expires_at = expires_at
            existing_otp.attempts = 0
            existing_otp.is_used = False
            existing_otp.updated_at = now_vn()
            await existing_otp.save()
        else:
            # Create new OTP
            email_otp = EmailOTP(
                email=data.email,
                otp_code=otp_code,
                otp_type="registration",
                expires_at=expires_at,
                created_at=now_vn(),
                updated_at=now_vn()
            )
            await email_otp.insert()
        
        # Send OTP email in background
        background_tasks.add_task(
            send_otp_email,
            email=data.email,
            otp=otp_code,
            otp_type="registration",
            full_name=user.full_name
        )
        
        background_tasks.add_task(
            logger.info,
            f"OTP resent to: {data.email}"
        )
        
        # Log security event
        background_tasks.add_task(
            log_security_event,
            event_type="otp_resent",
            user_id=str(user.id),
            email=data.email,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            details={"otp_type": "registration"},
            success=True
        )
        
        return {
            "message": "OTP sent successfully",
            "email": data.email,
            "expires_in_minutes": settings.OTP_EXPIRY_MINUTES
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Resend OTP error for {data.email}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to resend OTP"
        )

@router.post("/login", response_model=AccessToken)
@limiter.limit("5/minute")
async def login(
    data: LoginRequest,
    request: Request,
    background_tasks: BackgroundTasks
):
    """
    Login with email and password
    """
    try:
        # Authenticate user using repository
        user = await UserRepository.authenticate_user(data.email, data.password)
        
        if not user:
            # Log failed login attempt
            background_tasks.add_task(
                log_security_event,
                event_type="login_failed",
                email=data.email,
                ip_address=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent"),
                details={"reason": "invalid_credentials"},
                success=False
            )
            
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ErrorCode.INVALID_CREDENTIALS,
            )
        
        # Check if user is active
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=ErrorCode.USER_INACTIVE,
            )
        
        # Check if email is verified
        if not user.is_verified:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Please verify your email first",
            )
        
        # Get user roles and permissions for token
        actor_links = await UserActor.find(
            UserActor.user_id == user.id
        ).to_list()
        
        actor_ids = list({link.actor_id for link in actor_links})
        actors = []
        if actor_ids:
            actors = await Actor.find(
                {"_id": {"$in": actor_ids}, "is_active": True}
            ).to_list()
        
        # Get permissions
        permissions = []
        active_actor_ids = [actor.id for actor in actors]
        if active_actor_ids:
            from app.models.actor_permission import ActorPermission
            from app.models.permission import Permission
            
            perm_links = await ActorPermission.find(
                {"actor_id": {"$in": active_actor_ids}}
            ).to_list()
            permission_ids = list({link.permission_id for link in perm_links})
            if permission_ids:
                permissions = await Permission.find(
                    {"_id": {"$in": permission_ids}, "is_active": True}
                ).to_list()
        
        # Create token pair with scopes/permissions
        scopes = [f"role:{actor.name}" for actor in actors]
        scopes.extend([f"perm:{perm.name}" for perm in permissions])
        
        token_pair = create_token_pair(
            user=user,
            scopes=scopes
        )
        
        # Log successful login
        background_tasks.add_task(
            log_security_event,
            event_type="login_success",
            user_id=str(user.id),
            email=data.email,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            details={"login_method": "password"},
            success=True
        )
        
        background_tasks.add_task(
            logger.info,
            f"User logged in: {data.email}"
        )
        
        return AccessToken(
            access_token=token_pair.access_token,
            token_type=token_pair.token_type,
            expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            refresh_token=token_pair.refresh_token,
            refresh_token_expires_in=settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
            user=UserResponse(
                id=str(user.id),
                email=user.email,
                full_name=user.full_name,
                phone_number=user.phone_number,
                address=user.address,
                is_active=user.is_active,
                is_verified=user.is_verified,
                created_at=user.created_at
            )
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Login error for {data.email}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Login failed"
        )

@router.post("/logout", status_code=status.HTTP_200_OK)
async def logout(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    background_tasks: BackgroundTasks = None
):
    """
    Logout user by blacklisting token
    """
    try:
        token = await get_token_from_request(request)
        if token:
            # Blacklist the token
            await blacklist_token(token)
            
            # Log logout event
            if background_tasks:
                background_tasks.add_task(
                    log_security_event,
                    event_type="logout",
                    user_id=str(current_user.user.id),
                    email=current_user.user.email,
                    ip_address=request.client.host if request.client else None,
                    user_agent=request.headers.get("user-agent"),
                    success=True
                )
            
            logger.info(f"User logged out: {current_user.user.email}")
        
        return {"message": "Logged out successfully"}
        
    except Exception as e:
        logger.error(f"Logout error for user {current_user.user.email}: {e}")
        # Still return success even if logging fails
        return {"message": "Logged out successfully"}

@router.post("/refresh", response_model=AccessToken)
async def refresh_token(
    request: Request,
    background_tasks: BackgroundTasks
):
    try:
        from app.core.security import (
            get_token_from_request,
            decode_jwt_token,
            is_token_blacklisted,
            blacklist_token,
            create_access_token,
            create_token_pair
        )
        
        token = await get_token_from_request(request)
        if not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ErrorCode.INVALID_CREDENTIALS,
            )
        
        token_payload = decode_jwt_token(token)
        if not token_payload or token_payload.type != "refresh":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ErrorCode.INVALID_TOKEN_TYPE,
            )
        
        if await is_token_blacklisted(token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ErrorCode.TOKEN_EXPIRED,
            )
        
        # Get user
        user = await UserRepository.get_user_by_email(token_payload.email)
        if not user or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=ErrorCode.USER_NOT_FOUND,
            )
        
        await blacklist_token(token)
        
        token_pair = create_token_pair(
            user=user,
            scopes=token_payload.scopes or []
        )
        
        background_tasks.add_task(
            logger.info,
            f"Token refreshed for user: {user.email}"
        )
        
        # Log security event
        background_tasks.add_task(
            log_security_event,
            event_type="token_refreshed",
            user_id=str(user.id),
            email=user.email,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            success=True
        )
        
        return AccessToken(
            access_token=token_pair.access_token,
            token_type=token_pair.token_type,
            expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            refresh_token=token_pair.refresh_token,
            refresh_token_expires_in=settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
            user=UserResponse(
                id=str(user.id),
                email=user.email,
                full_name=user.full_name,
                phone_number=user.phone_number,
                address=user.address,
                is_active=user.is_active,
                is_verified=user.is_verified,
                created_at=user.created_at
            )
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Token refresh error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Token refresh failed"
        )

async def get_token_from_request(request: Request) -> Optional[str]:
    """Extract token from request"""
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header[7:]
    return None

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