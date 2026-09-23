import secrets
from typing import Optional

from fastapi import Request, Response, status

from app.core.config import settings
from app.core.errors import CustomError, ErrorCodes
from app.core.security import csrf_token_digest, decode_jwt_token
from app.dependencies.error_code import ErrorCode


def _cookie_seconds() -> int:
    return settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60


def set_auth_cookies(response: Response, refresh_token: str, csrf_token: str) -> None:
    response.set_cookie(
        key=settings.REFRESH_COOKIE_NAME,
        value=refresh_token,
        httponly=True,
        max_age=_cookie_seconds(),
        path=settings.REFRESH_COOKIE_PATH,
        domain=settings.REFRESH_COOKIE_DOMAIN,
        secure=settings.REFRESH_COOKIE_SECURE,
        samesite=settings.REFRESH_COOKIE_SAMESITE,
    )
    response.set_cookie(
        key=settings.CSRF_COOKIE_NAME,
        value=csrf_token,
        httponly=False,
        max_age=_cookie_seconds(),
        path=settings.CSRF_COOKIE_PATH,
        domain=settings.REFRESH_COOKIE_DOMAIN,
        secure=settings.REFRESH_COOKIE_SECURE,
        samesite=settings.REFRESH_COOKIE_SAMESITE,
    )
    response.headers[settings.CSRF_HEADER_NAME] = csrf_token
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


def clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(
        key=settings.REFRESH_COOKIE_NAME,
        httponly=True,
        path=settings.REFRESH_COOKIE_PATH,
        domain=settings.REFRESH_COOKIE_DOMAIN,
        secure=settings.REFRESH_COOKIE_SECURE,
        samesite=settings.REFRESH_COOKIE_SAMESITE,
    )
    response.delete_cookie(
        key=settings.CSRF_COOKIE_NAME,
        httponly=False,
        path=settings.CSRF_COOKIE_PATH,
        domain=settings.REFRESH_COOKIE_DOMAIN,
        secure=settings.REFRESH_COOKIE_SECURE,
        samesite=settings.REFRESH_COOKIE_SAMESITE,
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


def get_refresh_cookie(request: Request) -> Optional[str]:
    return request.cookies.get(settings.REFRESH_COOKIE_NAME)


def validate_refresh_csrf(request: Request, csrf_header: Optional[str] = None) -> str:
    refresh_token = get_refresh_cookie(request)
    csrf_cookie = request.cookies.get(settings.CSRF_COOKIE_NAME)
    csrf_header = csrf_header or request.headers.get(settings.CSRF_HEADER_NAME)

    if not refresh_token:
        raise CustomError(
            ErrorCodes.UNAUTHORIZED,
            ErrorCode.INVALID_REFRESH_TOKEN,
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    if not csrf_cookie or not csrf_header or not secrets.compare_digest(csrf_cookie, csrf_header):
        raise CustomError(
            ErrorCodes.FORBIDDEN,
            "CSRF validation failed",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    payload = decode_jwt_token(refresh_token)
    expected_hash = csrf_token_digest(csrf_header)
    if (
        not payload
        or payload.type != "refresh"
        or not payload.csrf_hash
        or not secrets.compare_digest(payload.csrf_hash, expected_hash)
    ):
        raise CustomError(
            ErrorCodes.UNAUTHORIZED,
            ErrorCode.INVALID_REFRESH_TOKEN,
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    return refresh_token
