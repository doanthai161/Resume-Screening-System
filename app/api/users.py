from fastapi import APIRouter, Depends, HTTPException, status, FastAPI
from app.core.security import get_current_user, require_permission
from app.schemas.user import UserCreate, UserUpdate, UserResponse, UserListRespponse
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
from app.dependencies.error_code import ErrorCode
from typing import List, Optional
from app.schemas.email_otp import RequestOTPRequest, VerifyOTPRegisterRequest
from app.core.email_otp import send_otp_email
from app.models.email_otp import EmailOTP
from app.utils.otp import generate_otp
from app.core.rate_limiter import limiter
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler

from datetime import datetime, timedelta, timezone

router = APIRouter()

app = FastAPI()

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

from fastapi import Request, BackgroundTasks

@router.post("/request-otp")
@limiter.limit("3/minute")
async def request_otp(
    request: Request,
    data: RequestOTPRequest,
    background_tasks: BackgroundTasks
):
    try:
        if await User.find_one(User.email == data.email):
            raise HTTPException(status_code=400, detail="Email already registered")

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
async def verify_otp_and_register(
    request: Request,
    data: VerifyOTPRegisterRequest
):
    try:
        otp_record = await EmailOTP.find_one(
            EmailOTP.email == data.email,
            EmailOTP.otp == data.otp,
            EmailOTP.is_used == False,
        )

        if not otp_record:
            raise HTTPException(status_code=400, detail="Invalid OTP")

        if otp_record.expires_at < datetime.now(timezone.utc):
            raise HTTPException(status_code=400, detail="OTP expired")

        if await User.find_one(User.email == data.email):
            raise HTTPException(status_code=400, detail="Email already registered")

        await User(
            email=data.email,
            full_name=data.full_name,
            hashed_password=get_password_hash(data.password),
        ).insert()

        otp_record.is_used = True
        await otp_record.save()

        return {"message": "Register successful"}
    except RateLimitExceeded:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=ErrorCode.TOO_MANY_REQUESTS,
        )
    
@router.post("/login", response_model=UserResponse)
async def login(data: UserCreate):
    user = await User.find_one(User.email == data.email)
    if not user or not verify_password(data.hashed_password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ErrorCode.INVALID_CREDENTIALS,
        )

    access_token_expires = timedelta(minutes=60)
    access_token = create_access_token(
        data={"sub": user.email}, expires_delta=access_token_expires
    )

    return UserResponse(
        id=str(user.id),
        email=user.email,
        full_name=user.full_name,
        access_token=access_token,
        token_type="bearer",
    )

from fastapi import Request

@router.post("/logout", status_code=status.HTTP_200_OK)
async def logout(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
):
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ErrorCode.INVALID_TOKEN,
        )

    token = auth_header.split(" ")[1]

    payload = decode_jwt_token(token)

    jti = payload.get("jti")
    exp = payload.get("exp")

    if not jti or not exp:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid token payload",
        )

    expires_at = datetime.fromtimestamp(exp, tz=timezone.utc)
    await blacklist_token(jti, expires_at)

    return {"message": "Logout successful"}
