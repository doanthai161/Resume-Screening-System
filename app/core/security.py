import os
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional, Set
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext

from app.api.dependencies.error_codes import ErrorCode
from app.models.nguoi_dung import NguoiDung
from app.models.nguoi_dung_tac_nhan import NguoiDungTacNhan
from app.models.quyen_han import QuyenHan
from app.models.tac_nhan import TacNhan
from app.models.tac_nhan_quyen_han import TacNhanQuyenHan
from dotenv import load_dotenv

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = os.getenv("ALGORITHM")

if not SECRET_KEY or not ALGORITHM:
    raise RuntimeError("Missing SECRET_KEY or ALGORITHM in environment variables")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()
blacklist_tokens: Set[str] = set()


class CurrentUser:
    """Wrapper providing user information together with related agents and permissions."""

    def __init__(self, user: NguoiDung, tac_nhan: List[TacNhan], quyen_han: List[QuyenHan]):
        self.user = user
        self.tac_nhan = tac_nhan
        self.quyen_han = quyen_han

    def __getattr__(self, item):
        return getattr(self.user, item)

    @property
    def user_id(self) -> Optional[str]:
        if getattr(self.user, "id", None) is None:
            return None
        return str(self.user.id)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=15))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_jwt_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None


def is_token_blacklisted(token: str) -> bool:
    return token in blacklist_tokens


def blacklist_token(token: str):
    blacklist_tokens.add(token)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> CurrentUser:
    token = credentials.credentials
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=ErrorCode.INVALID_CREDENTIALS,
        headers={"WWW-Authenticate": "Bearer"},
    )

    if is_token_blacklisted(token):
        raise credentials_exception
    
    try:
        payload = decode_jwt_token(token)
        if payload is None:
            raise credentials_exception
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = await NguoiDung.find_one(NguoiDung.email == email)
    if user is None:
        raise credentials_exception
    links = await NguoiDungTacNhan.find(
        NguoiDungTacNhan.nguoi_dung_id == user.id
    ).to_list()
    tac_nhan_ids = list({link.tac_nhan_id for link in links})

    tac_nhan: List[TacNhan] = []
    if tac_nhan_ids:
        tac_nhan = await TacNhan.find(
            {"_id": {"$in": tac_nhan_ids}, "hoat_dong": True}
        ).to_list()

    active_agent_ids = [agent.id for agent in tac_nhan]
    quyen_han: List[QuyenHan] = []
    if active_agent_ids:
        perm_links = await TacNhanQuyenHan.find(
            {"tac_nhan_id": {"$in": active_agent_ids}}
        ).to_list()
        quyen_han_ids = list({link.quyen_han_id for link in perm_links})
        if quyen_han_ids:
            quyen_han = await QuyenHan.find(
                {"_id": {"$in": quyen_han_ids}, "hoat_dong": True}
            ).to_list()

    return CurrentUser(user=user, tac_nhan=tac_nhan, quyen_han=quyen_han)


def require_permission(permission: str) -> Callable[[CurrentUser], CurrentUser]:
    async def dependency(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        user_permissions = {perm.ten for perm in current_user.quyen_han}
        if permission not in user_permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=ErrorCode.FORBIDDEN,
            )
        return current_user

    return dependency