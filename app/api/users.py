from contextvars import Token
from fastapi import APIRouter, Depends, HTTPException, status, FastAPI
from app.core.security import get_current_user, require_permission
from app.schemas.user import (
    AccessToken,
    LoginRequest,
    RegisterRequest,
    UserResponse,
    VerifyOTPResponse,
    UserListRespponse,
    UserUpdate,

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
from app.models.actor import Actor
from app.models.user_actor import UserActor
from app.core.config import settings
from bson import ObjectId

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
    default_actor = await Actor.find_one(Actor.name == settings.DEFAULT_ROLE_NAME)
    if not default_actor:
        background_tasks.add_task(logger.error, f"Default actor '{settings.DEFAULT_ROLE_NAME}' not found. Cannot assign to user '{data.email}'.")
        raise HTTPException(status_code=404, detail="Default actor not found")
    
    user_actor = UserActor(
        user_id=ObjectId(user.id),
        actor_id=ObjectId(default_actor.id),
        created_by=ObjectId(user.id),
    )
    await user_actor.insert()
    background_tasks.add_task(logger.info, f"Assigned default actor '{settings.DEFAULT_ROLE_NAME}' to user '{data.email}'.")

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


@router.get("/", response_model=UserListRespponse)
@limiter.limit("10/minute")
async def get_users(
    request: Request,
    background_tasks: BackgroundTasks,
    page: int = 1,
    size: int = 10,
    current_user: CurrentUser = Depends(
        require_permission("users:view")
    ),
):
    background_tasks.add_task(
        logger.info,
        f"User: {current_user.user_id} get users"
    )

    if page < 1 or size < 1:
        raise HTTPException(
            status_code=400,
            detail="page and size must be greater than 0"
        )

    skip = (page - 1) * size
    
    users = await User.find(User.is_active == True).to_list()
    total = len(users)
    return UserListRespponse(
        users=[
            UserResponse(
                id=str(user.id),
                email=user.email,
                full_name=user.full_name,
                phone_number=user.phone_number,
                address=user.address,
            ) for user in users
        ],
        total=total,
        page=page,
        size=size,
    )

@router.get("/detail_user", response_model=UserResponse)
@limiter.limit("10/minute")
async def get_users(
    request: Request,
    user_id: str,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(
        require_permission("users:view")
    ),
):
    try:
        uid = ObjectId(user_id)
    except:
        raise HTTPException(status_code=400, detail="Invalid user_id")
    
    background_tasks.add_task(
        logger.info,
        f"User: {current_user.user_id} get user_detail: {user_id}"
    )
    user = await User.find_one(
        {"_id": uid, "is_active": True}
    )
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    return UserResponse(
            id=str(user.id),
            email=user.email,
            full_name=user.full_name,
            phone_number=user.phone_number,
            address=user.address,
    )

@router.patch("/update-user/{user_id}", response_model=UserResponse)
@limiter.limit("10/minute")
async def update_user(
    request: Request,
    user_id: str,
    data: UserUpdate,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(
        get_current_user
    ),
):
    try:
        uid = ObjectId(user_id)
    except:
        raise HTTPException(status_code=400, detail="Invalid user_id")
    background_tasks.add_task(
        logger.info,
        f"User: {current_user.user_id} edit user: {user_id}"
    )
    if current_user.user_id != user_id:
        await require_permission("users:edit")(current_user)

    user = await User.find_one(
        {"_id": uid, "is_active": True}
    )
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    update_data = data.model_dump(exclude_unset=True)

    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")
    
    update_data.update({
        "updated_at": now_vn(),
    })

    background_tasks.add_task(
        logger.info,
        f"User {current_user.user_id} updating user_id: {user_id}: {list(update_data.keys())}"
    )
    user.set(update_data)
    await user.save()

    return UserResponse(
            id=str(user.id),
            email=user.email,
            full_name=user.full_name,
            phone_number=user.phone_number,
            address=user.address,
    )

@router.delete("/delete_user/{user_id}", status_code=200)
@limiter.limit("5/minute")
async def delete_user(
    request: Request,
    user_id: str,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(
        require_permission("users:delete")
    ),
):
    try:
        uid = ObjectId(user_id)
    except:
        raise HTTPException(status_code=400, detail="Invalid user_id")
    background_tasks.add_task(
        logger.info,
        f"User: {current_user.user_id} edit user: {user_id}"
    )
    
    result = await User.find_one(
        {"_id": uid, "is_active": True}
    ).update(
        {
            "$set": {
                "is_active": False,
                "updated_at": now_vn()
            }
        }
    )
    if result.matched_count ==0:
        raise HTTPException(status_code=404, detail="User not found or already deleted")
    
    background_tasks.add_task(
        logger.info,
        f"User {user_id} soft-deleted by user: {current_user.user_id}"
    )