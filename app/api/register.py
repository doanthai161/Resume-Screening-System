from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks, Request
from typing import Optional, Dict

from app.schemas.user import (
    AccessToken,
    LoginRequest,
    RegisterRequest,
    UserResponse,
    VerifyOTPResponse,
    VerifyOTPRegisterRequest,
)
from app.schemas.email_otp import RequestOTPRequest
from app.schemas.response import ApiResponse
from app.core.security import get_current_user, CurrentUser, get_token_from_request, blacklist_token
from app.core.errors import CustomError, ErrorCodes
from app.logs.logging_config import logger
from app.core.rate_limiter import limiter
from app.models.audit_log import AuditEventType

# Import the new AuthService
from app.services.auth_service import AuthService, log_security_event

router = APIRouter()

@router.post("/register", status_code=status.HTTP_201_CREATED, response_model=ApiResponse[UserResponse])
@limiter.limit("3/minute")
async def register(
    data: RegisterRequest,
    request: Request,
    background_tasks: BackgroundTasks
):
    try:
        user = await AuthService.register(data, request, background_tasks)
        
        user_response = UserResponse(
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
        return ApiResponse.ok(user_response)
        
    except CustomError:
        raise
    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"Registration validation error for {data.email}: {e}")
        raise CustomError(ErrorCodes.VALIDATION, str(e), status_code=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        logger.error(f"Registration error for {data.email}: {e}", exc_info=True)
        raise CustomError(
            ErrorCodes.INTERNAL, 
            "Registration failed. Please try again later.", 
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

@router.post("/verify-otp", response_model=ApiResponse[VerifyOTPResponse])
@limiter.limit("5/minute")
async def verify_otp(
    data: VerifyOTPRegisterRequest,
    request: Request,
    background_tasks: BackgroundTasks
):
    try:
        token_pair, user = await AuthService.verify_otp(data, request, background_tasks)
        
        response_data = VerifyOTPResponse(
            token=AccessToken(
                access_token=token_pair.access_token if hasattr(token_pair, 'access_token') else token_pair,
                token_type="bearer"
            ),
            success=True,
            user=UserResponse(
                id=str(user.id),
                email=user.email,
                full_name=user.full_name,
                message="Email verified successfully",
                phone_number=user.phone_number,
                address=user.address,
            )
        )
        return ApiResponse.ok(response_data)
        
    except CustomError:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"OTP verification error for {data.email}: {e}", exc_info=True)
        raise CustomError(
            ErrorCodes.INTERNAL, 
            "OTP verification failed", 
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

@router.post("/resend-otp", status_code=status.HTTP_200_OK, response_model=ApiResponse[Dict])
@limiter.limit("3/minute")
async def resend_otp(
    data: RequestOTPRequest,
    request: Request,
    background_tasks: BackgroundTasks
):
    try:
        from app.core.config import settings
        await AuthService.resend_otp(data, request, background_tasks)
        
        return ApiResponse.ok({
            "message": "OTP sent successfully",
            "email": data.email,
            "expires_in_minutes": settings.OTP_EXPIRY_MINUTES
        })
        
    except CustomError:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Resend OTP error for {data.email}: {e}", exc_info=True)
        raise CustomError(
            ErrorCodes.INTERNAL, 
            "Failed to resend OTP", 
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

@router.post("/login", response_model=ApiResponse[AccessToken])
@limiter.limit("5/minute")
async def login(
    data: LoginRequest,
    request: Request,
    background_tasks: BackgroundTasks
):
    try:
        from app.core.config import settings
        token_pair, user = await AuthService.login(data, request, background_tasks)
        
        response_data = AccessToken(
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
                message="login",
                is_active=user.is_active,
                is_verified=user.is_verified,
                created_at=user.created_at
            )
        )
        return ApiResponse.ok(response_data)
        
    except CustomError:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Login error for {data.email}: {e}", exc_info=True)
        raise CustomError(ErrorCodes.INTERNAL, "Login failed", status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)

@router.post("/logout", status_code=status.HTTP_200_OK, response_model=ApiResponse[Dict])
async def logout(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    background_tasks: BackgroundTasks = None
):
    try:
        token = await get_token_from_request(request)
        if token:
            await blacklist_token(token)
            
            if background_tasks:
                background_tasks.add_task(
                    log_security_event,
                    event_type=AuditEventType.USER_LOGOUT,
                    user_id=str(current_user.user.id),
                    event_name="logout",
                    email=current_user.user.email,
                    ip_address=request.client.host if request.client else None,
                    user_agent=request.headers.get("user-agent"),
                    success=True
                )
            
            logger.info(f"User logged out: {current_user.user.email}")
        
        return ApiResponse.ok({"message": "Logged out successfully"})
        
    except Exception as e:
        logger.error(f"Logout error for user {current_user.user.email}: {e}")
        return ApiResponse.ok({"message": "Logged out successfully"})

@router.post("/refresh", response_model=ApiResponse[AccessToken])
async def refresh_token(
    request: Request,
    background_tasks: BackgroundTasks
):
    try:
        from app.core.config import settings
        token = await get_token_from_request(request)
        if not token:
            raise CustomError(
                ErrorCodes.UNAUTHORIZED, 
                "No token provided", 
                status_code=status.HTTP_401_UNAUTHORIZED
            )
            
        token_pair, user = await AuthService.refresh_token(token, request, background_tasks)
        
        response_data = AccessToken(
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
        return ApiResponse.ok(response_data)
        
    except CustomError:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Token refresh error: {e}", exc_info=True)
        raise CustomError(
            ErrorCodes.INTERNAL, 
            "Token refresh failed", 
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )