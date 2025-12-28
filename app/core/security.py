import os
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional, Set
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext

from app.dependencies.error_code import ErrorCode
from app.models.user import User
from app.models.user_actor import UserActor
from app.models.permission import Permission
from app.models.actor import Actor
from app.models.actor_permission import ActorPermission
from dotenv import load_dotenv

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = os.getenv("ALGORITHM")

if not SECRET_KEY or not ALGORITHM:
    raise RuntimeError("Missing SECRET_KEY or ALGORITHM in environment variables")

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")
security = HTTPBearer()
blacklist_tokens: Set[str] = set()


class CurrentUser:
    """Wrapper providing user information together with related agents and permissions."""

    def __init__(self, user: User, actor: List[Actor], permission: List[Permission]):
        self.user = user
        self.actor = actor
        self.permission = permission

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

    user = await User.find_one(User.email == email)
    if user is None:
        raise credentials_exception
    links = await UserActor.find(
        UserActor.user_id == user.id
    ).to_list()
    actor_ids = list({link.actor_id for link in links})

    actor: List[Actor] = []
    if actor_ids:
        actor = await Actor.find(
            {"_id": {"$in": actor_ids}, "is_active": True}
        ).to_list()

    active_agent_ids = [agent.id for agent in actor]
    permission: List[Permission] = []
    if active_agent_ids:
        perm_links = await ActorPermission.find(
            {"actor_id": {"$in": active_agent_ids}}
        ).to_list()
        permission_ids = list({link.permission_id for link in perm_links})
        if permission_ids:
            permission = await Permission.find(
                {"_id": {"$in": permission_ids}, "is_active": True}
            ).to_list()

    return CurrentUser(user=user, actor=actor, permission=permission)


def require_permission(permission: str) -> Callable[[CurrentUser], CurrentUser]:
    async def dependency(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        user_permissions = {perm.name for perm in current_user.permission}
        if permission not in user_permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=ErrorCode.FORBIDDEN,
            )
        return current_user

    return dependency