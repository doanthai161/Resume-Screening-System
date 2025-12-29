from contextvars import Token
from fastapi import APIRouter, Depends, HTTPException, status, FastAPI
from app.core.security import get_current_user, require_permission
from app.schemas.user import (
    AccessToken,
    LoginRequest,
    RegisterRequest,
    UserResponse,
    VerifyOTPResponse,

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
)
from app.logs.logging_config import logger
from app.dependencies.error_code import ErrorCode
from typing import List, Optional
from app.schemas.email_otp import RequestOTPRequest, VerifyOTPRegisterRequest
from app.core.email_otp import send_otp_email
from app.models.email_otp import EmailOTP
from app.utils.otp import generate_otp
from app.core.rate_limiter import limiter
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler
from fastapi import Request, BackgroundTasks
from app.utils.time import now_vn
from datetime import datetime, timedelta, timezone

router = APIRouter()

app = FastAPI()

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


def ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt

@router.post("/request-otp")
@limiter.limit("3/minute")
async def request_otp(
    request: Request,
    data: RequestOTPRequest,
    background_tasks: BackgroundTasks
):
    try:
        if await User.find_one(User.email == data.email):
            raise HTTPException(status_code=400, detail=ErrorCode.EMAIL_ALREADY_REGISTERED)

        await EmailOTP.find(
            EmailOTP.email == data.email,
            EmailOTP.is_used == False
        ).update({"$set": {"is_used": True}})

        otp = generate_otp()

        await EmailOTP(
            email=data.email,
            otp=otp,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        ).insert()

        background_tasks.add_task(send_otp_email, data.email, otp)

        return {"message": "OTP sent"}
    except RateLimitExceeded:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=ErrorCode.TOO_MANY_REQUESTS,
        )


@router.post("/verify-otp-register")
@limiter.limit("5/minute")
async def verify_otp(
    request: Request,
    data: VerifyOTPRegisterRequest
):
    try:
        otp_record = await EmailOTP.find_one(
            EmailOTP.email == data.email,
            EmailOTP.otp == data.otp,
            EmailOTP.is_used == False,
        )
        expires_at = ensure_utc(otp_record.expires_at)

        if not otp_record:
            raise HTTPException(status_code=400, detail=ErrorCode.INVALID_OTP)

        now = datetime.now(timezone.utc)
        if expires_at <= now:
            raise HTTPException(status_code=400, detail=ErrorCode.OTP_EXPIRED)

        otp_record.is_used = True
        user = await User.find_one({"email": data.email, "is_active": False})
        if not user:
            raise HTTPException(
                status_code=400,
                detail=ErrorCode.USER_NOT_FOUND
            )
        if not verify_password(data.password, user.hashed_password):
            raise HTTPException(
                status_code=401,
                detail=ErrorCode.INVALID_CREDENTIALS
            )
        
        user.is_active = True
        await user.save()
        await otp_record.save()

        return {"message": "Register successful"}
    except RateLimitExceeded:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=ErrorCode.TOO_MANY_REQUESTS,
        )
    
@router.post("/login", response_model=VerifyOTPResponse)
@limiter.limit("5/minute")
async def login(request: Request, data: LoginRequest):
    user = await User.find_one({"email": data.email})
    if not user or not verify_password(data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ErrorCode.INVALID_CREDENTIALS,
        )
    if not user.is_active:
        logger.warning(f"Login attempt for inactive or unverified user: {data.email}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ErrorCode.USER_NOT_FOUND,
        )
    access_token_expires = timedelta(minutes=120)
    access_token = create_access_token(
        data={"sub": user.email}, expires_delta=access_token_expires
    )
    token_data = AccessToken(access_token=access_token)

    return VerifyOTPResponse(
        token=token_data,
        user=UserResponse(
            id=str(user.id),
            email=user.email,
            full_name=user.full_name,
            phone_number=user.phone_number,
            address=user.address,
        ),
    )

@router.post("/logout")
async def logout(
    request: Request, current_user: CurrentUser = Depends(get_current_user)
):
    try:
        logger.info(f"Logout endpoint called for user: {current_user.user_id}")
        auth_header = request.headers.get("authorization", "")
        token = auth_header.replace("Bearer ", "").strip()

        if token:
            blacklist_token(token)
            logger.info(f"Token blacklisted for user: {current_user.user_id}")

        return {"msg": "Logout successful"}
    except Exception as e:
        logger.error(f"Error during logout for user '{current_user.user_id}': {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=ErrorCode.INTERNAL_SERVER_ERROR
        )



@router.post("/register", status_code=status.HTTP_201_CREATED, response_model=UserResponse)
@limiter.limit("3/minute")
async def register(
    data: RegisterRequest,
    request: Request,
    background_tasks: BackgroundTasks
):
    if await User.find_one(User.email == data.email):
        raise HTTPException(
            status_code=400,
            detail=ErrorCode.EMAIL_ALREADY_REGISTERED,
        )
    print("User settings:", hasattr(User, "_document_settings"))


    user = User(
        email=data.email,
        full_name=data.full_name,
        hashed_password=get_password_hash(data.password),
        is_active=False,
    )
    await user.insert()

    otp = generate_otp()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)

    await EmailOTP(
        email=data.email,
        otp=otp,
        expires_at=expires_at,
    ).insert()

    background_tasks.add_task(send_otp_email, data.email, otp)

    return UserResponse(
        id=str(user.id),
        email=user.email,
        full_name=user.full_name,
        phone_number=user.phone_number,
        address=user.address,
    )
