from fastapi import APIRouter, HTTPException, status, BackgroundTasks, Header, Request, Response
from typing import Dict, Optional

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
from app.core.auth_cookies import clear_auth_cookies, set_auth_cookies, validate_refresh_csrf
from app.core.errors import CustomError, ErrorCodes
from app.logs.logging_config import logger
from app.core.rate_limiter import limiter
from app.core.config import settings

from app.services.auth_service import AuthService

router = APIRouter()


def _access_token_response(token_pair, user: UserResponse) -> AccessToken:
    return AccessToken(
        access_token=token_pair.access_token,
        token_type=token_pair.token_type,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=user,
    )


def _optional_bearer_token(request: Request) -> Optional[str]:
    authorization = request.headers.get("Authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() == "bearer" and token:
        return token.strip()
    return None

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
    response: Response,
    background_tasks: BackgroundTasks
):
    try:
        token_pair, user = await AuthService.verify_otp(data, request, background_tasks)
        
        user_response = UserResponse(
            id=str(user.id),
            email=user.email,
            full_name=user.full_name,
            message="Email verified successfully",
            phone_number=user.phone_number,
            address=user.address,
            is_active=user.is_active,
            is_verified=user.is_verified,
            created_at=user.created_at,
        )
        set_auth_cookies(response, token_pair.refresh_token, token_pair.csrf_token)
        response_data = VerifyOTPResponse(
            token=_access_token_response(token_pair, user_response),
            success=True,
            user=user_response,
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
    response: Response,
    background_tasks: BackgroundTasks
):
    try:
        token_pair, user = await AuthService.login(data, request, background_tasks)

        set_auth_cookies(response, token_pair.refresh_token, token_pair.csrf_token)
        response_data = _access_token_response(
            token_pair,
            UserResponse(
                id=str(user.id),
                email=user.email,
                full_name=user.full_name,
                phone_number=user.phone_number,
                address=user.address,
                message="login",
                is_active=user.is_active,
                is_verified=user.is_verified,
                created_at=user.created_at
            ),
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
@limiter.limit("10/minute")
async def logout(
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    csrf_token: Optional[str] = Header(default=None, alias=settings.CSRF_HEADER_NAME),
):
    try:
        refresh_token = validate_refresh_csrf(request, csrf_token)
        user = await AuthService.logout(
            refresh_token,
            _optional_bearer_token(request),
            request,
            background_tasks,
        )
        clear_auth_cookies(response)
        logger.info("User logged out: %s", user.email if user else "unknown")
        return ApiResponse.ok({"message": "Logged out successfully"})
    except (CustomError, HTTPException):
        raise
    except Exception as e:
        logger.error("Logout error: %s", e, exc_info=True)
        raise CustomError(
            ErrorCodes.INTERNAL,
            "Logout failed",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

@router.post("/refresh", response_model=ApiResponse[AccessToken])
@limiter.limit("10/minute")
async def refresh_token(
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    csrf_token: Optional[str] = Header(default=None, alias=settings.CSRF_HEADER_NAME),
):
    try:
        token = validate_refresh_csrf(request, csrf_token)
        token_pair, user = await AuthService.refresh_token(token, request, background_tasks)

        set_auth_cookies(response, token_pair.refresh_token, token_pair.csrf_token)
        response_data = _access_token_response(
            token_pair,
            UserResponse(
                id=str(user.id),
                email=user.email,
                full_name=user.full_name,
                phone_number=user.phone_number,
                address=user.address,
                is_active=user.is_active,
                is_verified=user.is_verified,
                created_at=user.created_at
            ),
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
