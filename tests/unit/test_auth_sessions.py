from unittest.mock import AsyncMock, patch

import pytest
from fastapi import BackgroundTasks, HTTPException, Request
from pydantic import ValidationError

from app.core.config import Settings, settings
from app.core.errors import CustomError
from app.core.security import (
    create_token_pair,
    get_current_user,
    is_refresh_session_revoked,
)
from app.services.auth_service import AuthService


class FakeRedis:
    def __init__(self):
        self.values: dict[str, tuple[str, int]] = {}

    async def set(self, key, value, *, ex, nx=False):
        if nx and key in self.values:
            return None
        self.values[key] = (value, ex)
        return True

    async def setex(self, key, ttl, value):
        self.values[key] = (value, ttl)
        return True

    async def exists(self, key):
        return int(key in self.values)


def _request(headers=None) -> Request:
    raw_headers = [
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in (headers or {}).items()
    ]
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/register/refresh",
            "headers": raw_headers,
            "client": ("127.0.0.1", 12345),
        }
    )


def test_production_requires_secure_refresh_cookie():
    with pytest.raises(ValidationError, match="REFRESH_COOKIE_SECURE"):
        Settings(
            _env_file=None,
            ENVIRONMENT="production",
            DEBUG=False,
            SECRET_KEY="s" * 48,
            CREATE_FIRST_SUPERUSER=False,
            CORS_ORIGINS="https://app.example.com",
            TRUSTED_PROXY_IPS="127.0.0.1",
            REFRESH_COOKIE_SECURE=False,
        )


@pytest.mark.asyncio
async def test_refresh_rotation_reuse_revokes_entire_session(monkeypatch, mock_user):
    redis = FakeRedis()
    monkeypatch.setattr("app.core.security.get_redis", lambda: redis)
    initial = create_token_pair(mock_user)

    with patch(
        "app.services.auth_service.UserRepository.get_user_by_email",
        new=AsyncMock(return_value=mock_user),
    ):
        rotated, _ = await AuthService.refresh_token(
            initial.refresh_token,
            _request(),
            BackgroundTasks(),
        )

        assert rotated.session_id == initial.session_id
        assert rotated.refresh_token != initial.refresh_token

        with pytest.raises(CustomError):
            await AuthService.refresh_token(
                initial.refresh_token,
                _request(),
                BackgroundTasks(),
            )

        assert await is_refresh_session_revoked(initial.session_id)

        with pytest.raises(CustomError):
            await AuthService.refresh_token(
                rotated.refresh_token,
                _request(),
                BackgroundTasks(),
            )


@pytest.mark.asyncio
async def test_logout_revokes_refresh_session(monkeypatch, mock_user):
    redis = FakeRedis()
    monkeypatch.setattr("app.core.security.get_redis", lambda: redis)
    token_pair = create_token_pair(mock_user)

    with patch(
        "app.services.auth_service.UserRepository.get_user_by_email",
        new=AsyncMock(return_value=mock_user),
    ):
        await AuthService.logout(
            token_pair.refresh_token,
            None,
            _request(),
            BackgroundTasks(),
        )

    assert await is_refresh_session_revoked(token_pair.session_id)
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(_request(), token=token_pair.access_token)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_refresh_token_cannot_authorize_protected_endpoint(monkeypatch, mock_user):
    redis = FakeRedis()
    monkeypatch.setattr("app.core.security.get_redis", lambda: redis)
    token_pair = create_token_pair(mock_user)

    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(_request(), token=token_pair.refresh_token)

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_login_sets_http_only_refresh_cookie_without_leaking_token(
    async_client,
    mock_user,
):
    token_pair = create_token_pair(mock_user)
    with patch(
        "app.api.register.AuthService.login",
        new=AsyncMock(return_value=(token_pair, mock_user)),
    ):
        response = await async_client.post(
            "/api/v1/register/login",
            json={"email": mock_user.email, "password": "StrongPassword"},
        )

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["access_token"] == token_pair.access_token
    assert "refresh_token" not in payload

    set_cookie_headers = response.headers.get_list("set-cookie")
    refresh_cookie = next(
        header for header in set_cookie_headers if header.startswith(f"{settings.REFRESH_COOKIE_NAME}=")
    )
    csrf_cookie = next(
        header for header in set_cookie_headers if header.startswith(f"{settings.CSRF_COOKIE_NAME}=")
    )
    assert "HttpOnly" in refresh_cookie
    assert "HttpOnly" not in csrf_cookie
    assert "SameSite=lax" in refresh_cookie
    assert response.headers["cache-control"] == "no-store"
    assert response.headers[settings.CSRF_HEADER_NAME] == token_pair.csrf_token


@pytest.mark.asyncio
async def test_refresh_requires_csrf_and_rotates_cookies(async_client, mock_user):
    initial = create_token_pair(mock_user)
    rotated = create_token_pair(mock_user, session_id=initial.session_id)
    async_client.cookies.set(
        settings.REFRESH_COOKIE_NAME,
        initial.refresh_token,
        domain="test.local",
        path=settings.REFRESH_COOKIE_PATH,
    )
    async_client.cookies.set(
        settings.CSRF_COOKIE_NAME,
        initial.csrf_token,
        domain="test.local",
        path=settings.CSRF_COOKIE_PATH,
    )

    with patch(
        "app.api.register.AuthService.refresh_token",
        new=AsyncMock(return_value=(rotated, mock_user)),
    ) as refresh_mock:
        rejected = await async_client.post("/api/v1/register/refresh")
        assert rejected.status_code == 403
        refresh_mock.assert_not_awaited()

        response = await async_client.post(
            "/api/v1/register/refresh",
            headers={settings.CSRF_HEADER_NAME: initial.csrf_token},
        )

    assert response.status_code == 200
    assert "refresh_token" not in response.json()["data"]
    assert async_client.cookies.get(settings.REFRESH_COOKIE_NAME) == rotated.refresh_token
    assert async_client.cookies.get(settings.CSRF_COOKIE_NAME) == rotated.csrf_token


@pytest.mark.asyncio
async def test_csrf_cookie_and_header_must_match_refresh_token_claim(
    async_client,
    mock_user,
):
    token_pair = create_token_pair(mock_user)
    async_client.cookies.set(
        settings.REFRESH_COOKIE_NAME,
        token_pair.refresh_token,
        domain="test.local",
        path=settings.REFRESH_COOKIE_PATH,
    )
    async_client.cookies.set(
        settings.CSRF_COOKIE_NAME,
        "attacker-controlled",
        domain="test.local",
        path=settings.CSRF_COOKIE_PATH,
    )

    with patch(
        "app.api.register.AuthService.refresh_token",
        new=AsyncMock(),
    ) as refresh_mock:
        response = await async_client.post(
            "/api/v1/register/refresh",
            headers={settings.CSRF_HEADER_NAME: "attacker-controlled"},
        )

    assert response.status_code == 401
    refresh_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_logout_requires_csrf_revokes_session_and_clears_cookies(
    async_client,
    mock_user,
):
    token_pair = create_token_pair(mock_user)
    async_client.cookies.set(
        settings.REFRESH_COOKIE_NAME,
        token_pair.refresh_token,
        domain="test.local",
        path=settings.REFRESH_COOKIE_PATH,
    )
    async_client.cookies.set(
        settings.CSRF_COOKIE_NAME,
        token_pair.csrf_token,
        domain="test.local",
        path=settings.CSRF_COOKIE_PATH,
    )

    with patch(
        "app.api.register.AuthService.logout",
        new=AsyncMock(return_value=mock_user),
    ) as logout_mock:
        response = await async_client.post(
            "/api/v1/register/logout",
            headers={
                settings.CSRF_HEADER_NAME: token_pair.csrf_token,
                "Authorization": f"Bearer {token_pair.access_token}",
            },
        )

    assert response.status_code == 200
    logout_mock.assert_awaited_once()
    assert settings.REFRESH_COOKIE_NAME not in async_client.cookies
    assert settings.CSRF_COOKIE_NAME not in async_client.cookies


@pytest.mark.asyncio
async def test_verify_otp_returns_access_token_and_sets_refresh_cookie(
    async_client,
    mock_user,
):
    token_pair = create_token_pair(mock_user)
    with patch(
        "app.api.register.AuthService.verify_otp",
        new=AsyncMock(return_value=(token_pair, mock_user)),
    ):
        response = await async_client.post(
            "/api/v1/register/verify-otp",
            json={"email": mock_user.email, "otp": "123456"},
        )

    assert response.status_code == 200
    token_payload = response.json()["data"]["token"]
    assert token_payload["access_token"] == token_pair.access_token
    assert "refresh_token" not in token_payload
    assert async_client.cookies.get(settings.REFRESH_COOKIE_NAME) == token_pair.refresh_token
