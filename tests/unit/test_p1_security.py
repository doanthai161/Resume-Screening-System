from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from bson import ObjectId
from fastapi import BackgroundTasks, HTTPException, Request

from app.core.config import settings
from app.core.errors import CustomError
from app.core.rate_limiter import limiter
from app.core.security import (
    issue_token_pair,
    get_current_user,
    revoke_refresh_session,
    is_refresh_session_revoked,
    get_password_hash,
)
from app.core.tenant_policy import branch_role
from app.models.auth_session import AuthSession
from app.models.company import Company
from app.models.company_branch import CompanyBranch
from app.models.user import User
from app.models.user_company import UserCompany
from app.repositories.user_repository import UserRepository
from app.services.auth_service import AuthService
from app.services.user_company_service import UserCompanyService
from app.utils.time import now_utc, ensure_utc

pytestmark = pytest.mark.asyncio


async def test_membership_listing_database_error_is_not_an_empty_success(monkeypatch):
    from app.repositories.user_company_repository import UserCompanyRepository

    def database_failure(*args, **kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(UserCompany, "find", database_failure)
    with pytest.raises(RuntimeError, match="database unavailable"):
        await UserCompanyRepository.list_branch_assignments(str(ObjectId()))


@pytest.fixture(autouse=True)
def isolated_auth(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(limiter, "enabled", False)
    monkeypatch.setattr("app.core.security.get_redis", lambda: None)
    monkeypatch.setattr("app.core.cache.get_redis", lambda: None)


async def user(**kwargs):
    key = str(ObjectId())
    return await User(
        email=f"{key}@example.com",
        username=key,
        phone_number=str(int(key, 16))[-15:],
        hashed_password=get_password_hash("OriginalPassword1"),
        is_active=True,
        is_verified=True,
        **kwargs,
    ).insert()


async def tenant(owner):
    key = str(ObjectId())
    company = await Company(
        user_id=owner.id,
        name=key,
        company_short_name=key,
        company_code=key,
        tax_code=key,
        email=owner.email,
        website="https://example.com",
    ).insert()
    branch = await CompanyBranch(
        company_id=company.id,
        bussiness_type="IT",
        branch_name=key,
        address="Test",
        company_size=1,
        working_days=["Monday"],
        created_by=owner.id,
    ).insert()
    return company, branch


async def membership(person, branch, role="member", **kwargs):
    return await UserCompany(
        user_id=person.id,
        company_branch_id=branch.id,
        assigned_by=branch.created_by,
        role=role,
        **kwargs,
    ).insert()


def request():
    return Request({"type": "http", "method": "POST", "path": "/", "headers": []})


async def test_global_directory_and_manual_verification_require_superuser(async_client):
    caller, target = await user(), await user()
    pair = await issue_token_pair(caller)
    headers = {"Authorization": f"Bearer {pair.access_token}"}
    for path in [
        "/api/v1/users/",
        "/api/v1/users/search?search_term=test",
        f"/api/v1/users/{target.id}",
    ]:
        response = await async_client.get(path, headers=headers)
        assert response.status_code == 403, (path, response.text)
    response = await async_client.post(
        f"/api/v1/users/verify/{target.id}", headers=headers
    )
    assert response.status_code == 403


async def test_manual_verify_does_not_activate_and_otp_cannot_unlock():
    target = await user()
    await User.find_one({"_id": target.id}).update(
        {"$set": {"is_verified": False, "is_active": False}}
    )
    assert await UserRepository.verify_user(str(target.id))
    assert not (await User.get(target.id)).is_active
    await User.find_one({"_id": target.id}).update(
        {"$set": {"is_verified": False, "deactivated_at": now_utc()}}
    )
    assert not await UserRepository.verify_user(
        str(target.id), activate_registration=True
    )
    assert not (await User.get(target.id)).is_active


@pytest.mark.parametrize("new_role", ["manager", "admin", "owner", "arbitrary"])
async def test_manager_cannot_promote_self_or_grant_equal_role(new_role):
    owner, manager, member = await user(), await user(), await user()
    _, branch = await tenant(owner)
    await membership(manager, branch, "manager")
    for target in [manager, member]:
        with pytest.raises(CustomError):
            await UserCompanyService._authorize_change(
                str(branch.id), str(target.id), str(manager.id), new_role
            )


async def test_manager_can_assign_member_but_cannot_change_admin_or_owner():
    owner, manager, member = await user(), await user(), await user()
    _, branch = await tenant(owner)
    await membership(manager, branch, "manager")
    await UserCompanyService._authorize_change(
        str(branch.id), str(member.id), str(manager.id), "member"
    )
    for target, old in [(member, "admin"), (owner, "member")]:
        with pytest.raises(CustomError):
            await UserCompanyService._authorize_change(
                str(branch.id), str(target.id), str(manager.id), old_role=old
            )


@pytest.mark.parametrize(
    "boundary", ["expired", "future", "revoked", "branch", "company"]
)
async def test_membership_and_parent_state_are_read_fresh(boundary):
    owner, member = await user(), await user()
    company, branch = await tenant(owner)
    link = await membership(member, branch)
    assert await branch_role(str(member.id), str(branch.id)) == "member"
    if boundary == "expired":
        await link.set({"end_date": now_utc() - timedelta(seconds=1)})
    elif boundary == "future":
        await link.set({"start_date": now_utc() + timedelta(days=1)})
    elif boundary == "revoked":
        await link.set({"is_active": False})
    else:
        await (branch if boundary == "branch" else company).set({"is_active": False})
    assert await branch_role(str(member.id), str(branch.id)) is None


async def test_user_branch_listing_filters_every_tenant():
    owner, other_owner, target = await user(), await user(), await user()
    _, allowed = await tenant(owner)
    _, forbidden = await tenant(other_owner)
    await membership(target, allowed)
    await membership(target, forbidden)
    rows = await UserCompanyService.list_user_branches(str(target.id), True, owner)
    assert [row.company_branch.id for row in rows] == [str(allowed.id)]


async def test_validation_error_is_json_and_does_not_echo_password(
    async_client, caplog
):
    secret = "secretshort"
    response = await async_client.post(
        "/api/v1/users/password/reset/confirm",
        json={"token": "x", "new_password": secret},
    )
    # Password validator raises ValueError; its ctx and input must never be serialized/logged.
    assert response.status_code == 422, response.text
    assert secret not in response.text
    assert secret not in caplog.text
    assert "input" not in response.text


async def test_revocation_survives_empty_redis_and_old_logout_does_not_shorten_session():
    target = await user()
    pair = await issue_token_pair(target)
    before = await AuthSession.find_one({"sid": pair.session_id})
    await revoke_refresh_session(pair.session_id, 1)
    await revoke_refresh_session(pair.session_id, 1)
    after = await AuthSession.find_one({"sid": pair.session_id})
    assert after.expires_at == before.expires_at
    assert await is_refresh_session_revoked(pair.session_id)
    with pytest.raises(HTTPException):
        await get_current_user(request(), token=pair.access_token)
    # Losing the registry also fails closed, rather than treating absence as valid.
    await after.delete()
    assert await is_refresh_session_revoked(pair.session_id)


async def test_refresh_has_fixed_absolute_expiry_and_replay_revokes():
    target = await user()
    initial = await issue_token_pair(target)
    before = await AuthSession.find_one({"sid": initial.session_id})
    rotated, _ = await AuthService.refresh_token(
        initial.refresh_token, request(), BackgroundTasks()
    )
    after = await AuthSession.find_one({"sid": initial.session_id})
    assert before.expires_at == after.expires_at
    assert ensure_utc(after.expires_at) > now_utc()
    with pytest.raises(CustomError):
        await AuthService.refresh_token(
            initial.refresh_token, request(), BackgroundTasks()
        )
    with pytest.raises(CustomError):
        await AuthService.refresh_token(
            rotated.refresh_token, request(), BackgroundTasks()
        )


async def test_production_redis_outage_is_503_without_revoking(monkeypatch):
    target = await user()
    pair = await issue_token_pair(target)
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    with pytest.raises(CustomError) as exc:
        await AuthService.refresh_token(
            pair.refresh_token, request(), BackgroundTasks()
        )
    assert exc.value.status_code == 503
    assert (await AuthSession.find_one({"sid": pair.session_id})).revoked_at is None


async def test_reset_only_latest_token_valid_and_single_use():
    target = await user()
    first = await UserRepository.generate_password_reset_token(target.email)
    second = await UserRepository.generate_password_reset_token(target.email)
    assert not await UserRepository.reset_password(first, "NewPassword1")
    assert await UserRepository.reset_password(second, "NewPassword1")
    assert not await UserRepository.reset_password(second, "AnotherPassword1")
    assert (await User.get(target.id)).auth_version == 1


async def test_password_change_and_deactivation_invalidate_reset():
    target = await user()
    token = await UserRepository.generate_password_reset_token(target.email)
    assert await UserRepository.change_password(
        str(target.id), "OriginalPassword1", "NewPassword1"
    )
    assert not await UserRepository.reset_password(token, "AttackerPassword1")
    token = await UserRepository.generate_password_reset_token(target.email)
    await User.find_one({"_id": target.id}).update({"$set": {"is_active": False}})
    assert not await UserRepository.reset_password(token, "AttackerPassword1")


async def test_readiness_requires_redis_in_production_but_liveness_does_not(
    async_client, monkeypatch
):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(
        "app.core.database.check_connection", AsyncMock(return_value=True)
    )
    monkeypatch.setattr("app.core.redis.get_redis", lambda: None)
    assert (await async_client.get("/ready")).status_code == 503
    assert (await async_client.get("/live")).status_code == 200


@pytest.mark.parametrize(
    "uri",
    [
        "mongodb://localhost:27017",
        "mongodb://localhost:27017/",
        "mongodb://localhost:27017/?replicaSet=rs0",
    ],
)
async def test_mongo_uri_without_database_has_no_double_slash(uri, monkeypatch):
    monkeypatch.setattr(settings, "MONGODB_URI", uri)
    monkeypatch.setattr(settings, "MONGODB_DB_NAME", "test_database")
    expected = "mongodb://localhost:27017/test_database"
    if "?" in uri:
        expected += "?replicaSet=rs0"
    assert settings.MONGODB_URL == expected


async def test_stale_login_does_not_restore_password_or_auth_version(monkeypatch):
    target = await user()
    assert await UserRepository.change_password(
        str(target.id), "OriginalPassword1", "NewPassword1"
    )
    before = await User.get(target.id)
    monkeypatch.setattr(
        UserRepository, "get_user_by_email", AsyncMock(return_value=target)
    )
    await UserRepository.authenticate_user(target.email, "OriginalPassword1")
    after = await User.get(target.id)
    assert after.auth_version == before.auth_version == 1
    assert after.hashed_password == before.hashed_password
