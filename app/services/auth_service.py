from typing import Optional, Tuple, Dict, Any
import secrets
from datetime import datetime, timedelta, timezone
from bson import ObjectId
from fastapi import status, BackgroundTasks, Request
from pymongo.errors import DuplicateKeyError

from app.models.user import User
from app.models.actor import Actor
from app.models.user_actor import UserActor
from app.models.email_otp import EmailOTP
from app.schemas.user import RegisterRequest, VerifyOTPRegisterRequest, LoginRequest
from app.schemas.email_otp import RequestOTPRequest
from app.core.security import (
    password_strength_check,
    create_token_pair,
    decode_jwt_token,
    consume_refresh_token,
    is_refresh_session_revoked,
    revoke_refresh_session,
    blacklist_token,
)
from app.core.errors import CustomError, ErrorCodes
from app.dependencies.error_code import ErrorCode
from app.repositories.user_repository import UserRepository
from app.core.config import settings
from app.utils.time import now_utc, ensure_utc
from app.utils.otp import generate_otp, hash_otp, verify_otp_hash
from app.core.email_otp import send_otp_email
from app.models.audit_log import AuditEventType
from app.logs.logging_config import logger

async def log_security_event(
    event_type: AuditEventType,
    event_name: str,
    user_id: Optional[str] = None,
    description: Optional[str] = None,
    email: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    details: Optional[Dict] = None,
    success: bool = True
):
    try:
        from app.services.audit_log_service import AuditLogService
        await AuditLogService.log_security_event(
            event_type=event_type,
            user_id=user_id,
            event_name=event_name,
            user_email=email,
            user_ip=ip_address,
            user_agent=user_agent,
            details=details or {},
            success=success
        )
    except Exception as e:
        logger.error(f"Failed to log security event: {e}", exc_info=True)


class AuthService:
    @staticmethod
    async def register(
        data: RegisterRequest, 
        request: Request, 
        background_tasks: BackgroundTasks
    ) -> User:
        password_check = password_strength_check(data.password)
        if not password_check["is_valid"]:
            raise CustomError(
                ErrorCodes.VALIDATION, 
                "Password does not meet requirements", 
                status_code=status.HTTP_400_BAD_REQUEST,
                details=password_check["issues"]
            )
        
        existing_user = await UserRepository.get_user_by_email(data.email)
        if existing_user:
            raise CustomError(
                ErrorCodes.BAD_REQUEST, 
                ErrorCode.EMAIL_ALREADY_REGISTERED, 
                status_code=status.HTTP_400_BAD_REQUEST
            )
        
        if data.phone_number:
            existing_phone_user = await User.find_one({"phone_number": data.phone_number})
            if existing_phone_user:
                raise CustomError(
                    ErrorCodes.BAD_REQUEST, 
                    ErrorCode.PHONE_ALREADY_REGISTERED, 
                    status_code=status.HTTP_400_BAD_REQUEST
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
            await UserRepository.hard_delete_user(str(user.id))
            raise CustomError(
                ErrorCodes.INTERNAL,
                "Registration role is not configured",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        else:
            try:
                user_actor = UserActor(
                    user_id=ObjectId(user.id),
                    actor_id=ObjectId(default_actor.id),
                    created_by=ObjectId(user.id),
                    created_at=now_utc()
                )
                await user_actor.insert()
                background_tasks.add_task(
                    logger.info, 
                    f"Assigned default actor '{settings.CANDIDATE_ROLE_NAME}' to user '{data.email}'."
                )
            except DuplicateKeyError:
                pass
            except Exception:
                await UserRepository.hard_delete_user(str(user.id))
                logger.exception("Failed to assign registration role")
                raise CustomError(
                    ErrorCodes.INTERNAL,
                    "Could not complete registration",
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )
                
        otp_code = generate_otp()
        expires_at = now_utc() + timedelta(minutes=settings.OTP_EXPIRY_MINUTES)
        
        existing_otp = await EmailOTP.find_one({
            "email": data.email,
            "otp_type": "registration",
            "is_used": False
        })
        
        if existing_otp:
            existing_otp.otp_code = None
            existing_otp.otp_hash = hash_otp(str(data.email), "registration", otp_code)
            existing_otp.expires_at = expires_at
            existing_otp.attempts = 0
            existing_otp.is_used = False
            existing_otp.updated_at = now_utc()
            await existing_otp.save()
        else:
            email_otp = EmailOTP(
                email=data.email,
                otp_hash=hash_otp(str(data.email), "registration", otp_code),
                otp_type="registration",
                expires_at=expires_at,
                created_at=now_utc(),
                updated_at=now_utc()
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
            event_type=AuditEventType.USER_REGISTER,
            event_name="User Registered",
            description="User registered via email",
            user_id=str(user.id),
            email=data.email,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            details={
                "email": data.email,
                "registration_method": "email",
                "has_phone": bool(data.phone_number)
            },
            success=True
        )
        
        return user

    @staticmethod
    async def verify_otp(
        data: VerifyOTPRegisterRequest,
        request: Request,
        background_tasks: BackgroundTasks
    ) -> Tuple[Any, User]:
        otp_record = await EmailOTP.find_one({
            "email": data.email,
            "otp_type": "registration",
            "is_used": False
        })
        
        if not otp_record:
            raise CustomError(ErrorCodes.NOT_FOUND, ErrorCode.OTP_NOT_FOUND, status_code=status.HTTP_400_BAD_REQUEST)
        
        if otp_record.is_used:
            raise CustomError(ErrorCodes.BAD_REQUEST, ErrorCode.OTP_ALREADY_USED, status_code=status.HTTP_400_BAD_REQUEST)
        
        if otp_record.is_expired:
            raise CustomError(ErrorCodes.BAD_REQUEST, ErrorCode.OTP_EXPIRED, status_code=status.HTTP_400_BAD_REQUEST)
        
        if not otp_record.can_attempt:
            raise CustomError(ErrorCodes.BAD_REQUEST, ErrorCode.OTP_MAX_ATTEMPTS, status_code=status.HTTP_400_BAD_REQUEST)
        
        valid_otp = (
            verify_otp_hash(str(data.email), "registration", data.otp, otp_record.otp_hash)
            if otp_record.otp_hash
            else secrets.compare_digest(otp_record.otp_code or "", data.otp)
        )
        if not valid_otp:
            await EmailOTP.find_one(
                {
                    "_id": otp_record.id,
                    "is_used": False,
                    "attempts": {"$lt": otp_record.max_attempts},
                }
            ).update({"$inc": {"attempts": 1}, "$set": {"updated_at": now_utc()}})
            remaining_attempts = max(0, otp_record.max_attempts - otp_record.attempts - 1)
            raise CustomError(
                ErrorCodes.BAD_REQUEST, 
                ErrorCode.INVALID_OTP, 
                status_code=status.HTTP_400_BAD_REQUEST,
                details={"remaining_attempts": remaining_attempts}
            )
        
        used_result = await EmailOTP.find_one(
            {
                "_id": otp_record.id,
                "is_used": False,
                "attempts": otp_record.attempts,
            }
        ).update({"$set": {"is_used": True, "updated_at": now_utc()}})
        if not used_result or getattr(used_result, "modified_count", 0) != 1:
            raise CustomError(
                ErrorCodes.BAD_REQUEST,
                ErrorCode.OTP_ALREADY_USED,
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        
        user = await UserRepository.get_user_by_email(data.email)
        if not user:
            raise CustomError(ErrorCodes.NOT_FOUND, ErrorCode.USER_NOT_FOUND, status_code=status.HTTP_404_NOT_FOUND)
        
        success = await UserRepository.verify_user(str(user.id))
        if not success:
            raise CustomError(ErrorCodes.INTERNAL, "Failed to verify user", status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)

        user = await UserRepository.get_user_by_email(data.email)
        if not user or not user.is_active or not user.is_verified:
            raise CustomError(
                ErrorCodes.INTERNAL,
                "Verified user could not be reloaded",
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        
        token_pair = create_token_pair(
            user=user,
            scopes=[]
        )
        
        background_tasks.add_task(
            log_security_event,
            event_type=AuditEventType.USER_EMAIL_VERIFY,
            event_name="rerify_otp",
            user_id=str(user.id),
            email=data.email,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            details={"verification_method": "otp"},
            success=True
        )
        
        background_tasks.add_task(logger.info, f"User email verified: {data.email}")
        
        return token_pair, user

    @staticmethod
    async def resend_otp(
        data: RequestOTPRequest,
        request: Request,
        background_tasks: BackgroundTasks
    ) -> None:
        user = await UserRepository.get_user_by_email(data.email)
        if not user:
            raise CustomError(ErrorCodes.NOT_FOUND, ErrorCode.USER_NOT_FOUND, status_code=status.HTTP_404_NOT_FOUND)
        
        if user.is_verified:
            raise CustomError(ErrorCodes.BAD_REQUEST, "User already verified", status_code=status.HTTP_400_BAD_REQUEST)
        
        otp_code = generate_otp()
        expires_at = now_utc() + timedelta(minutes=settings.OTP_EXPIRY_MINUTES)
        
        existing_otp = await EmailOTP.find_one({
            "email": data.email,
            "otp_type": "registration",
            "is_used": False
        })
        
        if existing_otp:
            time_since_last_send = now_utc() - ensure_utc(existing_otp.updated_at)
            if time_since_last_send < timedelta(seconds=30):
                raise CustomError(
                    ErrorCodes.RATE_LIMIT, 
                    "Please wait before requesting another OTP", 
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS
                )
            
            existing_otp.otp_code = None
            existing_otp.otp_hash = hash_otp(str(data.email), "registration", otp_code)
            existing_otp.expires_at = expires_at
            existing_otp.attempts = 0
            existing_otp.is_used = False
            existing_otp.updated_at = now_utc()
            await existing_otp.save()
        else:
            email_otp = EmailOTP(
                email=data.email,
                otp_hash=hash_otp(str(data.email), "registration", otp_code),
                otp_type="registration",
                expires_at=expires_at,
                created_at=now_utc(),
                updated_at=now_utc()
            )
            await email_otp.insert()
        
        background_tasks.add_task(logger.info, f"OTP resent to: {data.email}")
        background_tasks.add_task(
            send_otp_email,
            email=data.email,
            otp=otp_code,
            otp_type="registration",
            full_name=user.full_name,
        )
        
        background_tasks.add_task(
            log_security_event,
            event_type=AuditEventType.OTP_RESENT,
            event_name="resend otp",
            user_id=str(user.id),
            email=data.email,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            details={"otp_type": "registration"},
            success=True
        )

    @staticmethod
    async def login(
        data: LoginRequest,
        request: Request,
        background_tasks: BackgroundTasks
    ) -> Tuple[Any, User]:
        user = await UserRepository.authenticate_user(data.email, data.password)
        
        if not user:
            background_tasks.add_task(
                log_security_event,
                event_type=AuditEventType.USER_LOGIN_FAILED,
                event_name="check existing user in login",
                email=data.email,
                ip_address=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent"),
                details={"reason": "invalid_credentials"},
                success=False
            )
            raise CustomError(ErrorCodes.UNAUTHORIZED, ErrorCode.INVALID_CREDENTIALS, status_code=status.HTTP_401_UNAUTHORIZED)
        
        if not user.is_active:
            raise CustomError(ErrorCodes.FORBIDDEN, ErrorCode.USER_INACTIVE, status_code=status.HTTP_403_FORBIDDEN)
        
        if not user.is_verified:
            raise CustomError(ErrorCodes.FORBIDDEN, "Please verify your email first", status_code=status.HTTP_403_FORBIDDEN)
        
        actor_links = await UserActor.find(UserActor.user_id == user.id).to_list()
        actor_ids = list({link.actor_id for link in actor_links})
        actors = []
        if actor_ids:
            actors = await Actor.find({"_id": {"$in": actor_ids}, "is_active": True}).to_list()
        
        permissions = []
        active_actor_ids = [actor.id for actor in actors]
        if active_actor_ids:
            from app.models.actor_permission import ActorPermission
            from app.models.permission import Permission
            
            perm_links = await ActorPermission.find({"actor_id": {"$in": active_actor_ids}}).to_list()
            permission_ids = list({link.permission_id for link in perm_links})
            if permission_ids:
                permissions = await Permission.find({"_id": {"$in": permission_ids}, "is_active": True}).to_list()
        
        scopes = [f"role:{actor.name}" for actor in actors]
        scopes.extend([f"perm:{perm.name}" for perm in permissions])
        
        token_pair = create_token_pair(user=user, scopes=scopes)
        
        background_tasks.add_task(
            log_security_event,
            event_type=AuditEventType.USER_LOGIN,
            event_name="login",
            user_id=str(user.id),
            email=data.email,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            details={"login_method": "password"},
            success=True
        )
        
        background_tasks.add_task(logger.info, f"User logged in: {data.email}")
        
        return token_pair, user

    @staticmethod
    async def refresh_token(
        token: str,
        request: Request,
        background_tasks: BackgroundTasks
    ) -> Tuple[Any, User]:
        token_payload = decode_jwt_token(token)
        if not token_payload or token_payload.type != "refresh" or not token_payload.sid:
            raise CustomError(ErrorCodes.UNAUTHORIZED, ErrorCode.INVALID_TOKEN_TYPE, status_code=status.HTTP_401_UNAUTHORIZED)

        if await is_refresh_session_revoked(token_payload.sid):
            raise CustomError(
                ErrorCodes.UNAUTHORIZED,
                ErrorCode.TOKEN_EXPIRED,
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        
        user = await UserRepository.get_user_by_email(token_payload.email)
        if not user or not user.is_active:
            raise CustomError(ErrorCodes.UNAUTHORIZED, ErrorCode.USER_NOT_FOUND, status_code=status.HTTP_401_UNAUTHORIZED)
        if token_payload.auth_version != user.auth_version:
            raise CustomError(
                ErrorCodes.UNAUTHORIZED,
                ErrorCode.TOKEN_EXPIRED,
                status_code=status.HTTP_401_UNAUTHORIZED,
            )

        remaining_seconds = None
        if token_payload.exp:
            remaining_seconds = max(
                1,
                int(token_payload.exp - datetime.now(timezone.utc).timestamp()),
            )
        if not await consume_refresh_token(token, remaining_seconds):
            # Reuse of any token in a rotation family revokes the entire session.
            await revoke_refresh_session(token_payload.sid)
            raise CustomError(
                ErrorCodes.UNAUTHORIZED,
                ErrorCode.TOKEN_EXPIRED,
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        
        token_pair = create_token_pair(
            user=user,
            scopes=token_payload.scopes or [],
            session_id=token_payload.sid,
        )
        
        background_tasks.add_task(logger.info, f"Token refreshed for user: {user.email}")
        
        background_tasks.add_task(
            log_security_event,
            event_type=AuditEventType.REFRESH_TOKEN,
            event_name="refresh_token",
            user_id=str(user.id),
            email=user.email,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            success=True
        )
        
        return token_pair, user

    @staticmethod
    async def logout(
        refresh_token: str,
        access_token: Optional[str],
        request: Request,
        background_tasks: Optional[BackgroundTasks],
    ) -> Optional[User]:
        refresh_payload = decode_jwt_token(refresh_token)
        if (
            not refresh_payload
            or refresh_payload.type != "refresh"
            or not refresh_payload.sid
        ):
            raise CustomError(
                ErrorCodes.UNAUTHORIZED,
                ErrorCode.INVALID_REFRESH_TOKEN,
                status_code=status.HTTP_401_UNAUTHORIZED,
            )

        remaining_seconds = settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60
        if refresh_payload.exp:
            remaining_seconds = max(
                1,
                int(refresh_payload.exp - datetime.now(timezone.utc).timestamp()),
            )

        if not await revoke_refresh_session(refresh_payload.sid, remaining_seconds):
            raise CustomError(
                ErrorCodes.INTERNAL,
                "Could not revoke refresh session",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        # Mark the current refresh token consumed as defense in depth. The
        # session revocation above remains authoritative for the whole family.
        await consume_refresh_token(refresh_token, remaining_seconds)

        if access_token:
            access_payload = decode_jwt_token(access_token)
            if (
                access_payload
                and access_payload.type == "access"
                and access_payload.sid == refresh_payload.sid
            ):
                access_remaining = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
                if access_payload.exp:
                    access_remaining = max(
                        1,
                        int(access_payload.exp - datetime.now(timezone.utc).timestamp()),
                    )
                await blacklist_token(access_token, access_remaining)

        user = await UserRepository.get_user_by_email(refresh_payload.email)
        if background_tasks:
            background_tasks.add_task(
                log_security_event,
                event_type=AuditEventType.USER_LOGOUT,
                event_name="logout",
                user_id=str(user.id) if user else refresh_payload.user_id,
                email=user.email if user else refresh_payload.email,
                ip_address=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent"),
                details={"session_id": refresh_payload.sid},
                success=True,
            )
        return user
