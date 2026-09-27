"""Real router/service/ODM authorization checks against the isolated mock DB."""

from unittest.mock import AsyncMock

import pytest
from bson import ObjectId

from app.core import cache
from app.core.config import settings
from app.core.database import (
    _ensure_default_actors,
    _ensure_default_permissions,
    _revoke_legacy_candidate_user_permissions,
)
from app.core.errors import CustomError
from app.core.rate_limiter import limiter
from app.core.security import CurrentUser, issue_token_pair
from app.models.actor import Actor
from app.models.actor_permission import ActorPermission
from app.models.database_migration import DatabaseMigration
from app.models.permission import Permission
from app.models.user import User
from app.models.user_actor import UserActor
from app.schemas.user import UserUpdate
from app.services.user_actor_service import UserActorService
from app.services.user_service import UserService
from app.services.actor_service import ActorService
from app.services.permission_service import PermissionService
from app.services.actor_permission_service import ActorPermissionService
from app.schemas.actor import ActorCreate, ActorUpdate
from app.schemas.permission import PermissionCreate, PermissionUpdate


async def make_user(*, superuser=False):
    key = str(ObjectId())
    return await User(
        email=f"{key}@example.com",
        username=key,
        phone_number=str(int(key, 16))[-15:],
        hashed_password="unused",
        is_active=True,
        is_verified=True,
        is_superuser=superuser,
    ).insert()


@pytest.fixture
async def policy_setup(mock_db, monkeypatch):
    monkeypatch.setattr(limiter, "enabled", False)
    monkeypatch.setattr("app.core.security.get_redis", lambda: None)
    await _ensure_default_permissions()
    await _ensure_default_actors()
    candidate = await make_user()
    role = await Actor.find_one(Actor.name == settings.CANDIDATE_ROLE_NAME)
    await UserActor(
        user_id=candidate.id, actor_id=role.id, created_by=candidate.id
    ).insert()
    admin_role = await Actor.find_one(Actor.name == settings.ADMIN_ROLE_NAME)
    # Model an existing deployment that has not migrated, including stale grants.
    for name in ["users:view", "users:edit"]:
        permission = await Permission.find_one(Permission.name == name)
        await ActorPermission(actor_id=role.id, permission_id=permission.id).insert()
    return candidate, role, admin_role


async def headers(user):
    return {"Authorization": f"Bearer {(await issue_token_pair(user)).access_token}"}


@pytest.mark.asyncio
async def test_admin_can_delete_ordinary_user_but_not_self(async_client, policy_setup):
    _, _, admin_role = policy_setup
    caller, target = await make_user(), await make_user()
    await UserActor(
        user_id=caller.id, actor_id=admin_role.id, created_by=caller.id
    ).insert()
    auth = await headers(caller)
    response = await async_client.delete(f"/api/v1/users/{target.id}", headers=auth)
    assert response.status_code == 204
    assert not (await User.get(target.id)).is_active
    response = await async_client.delete(
        f"/api/v1/users/hard/{target.id}", headers=auth
    )
    assert response.status_code == 204
    assert await User.get(target.id) is None
    response = await async_client.delete(f"/api/v1/users/{caller.id}", headers=auth)
    assert response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("super_caller", [False, True])
@pytest.mark.parametrize("operation", ["delete", "hard", "bulk"])
async def test_removal_endpoints_never_remove_superusers(
    async_client, policy_setup, super_caller, operation
):
    _, _, admin_role = policy_setup
    caller = await make_user(superuser=super_caller)
    await UserActor(
        user_id=caller.id, actor_id=admin_role.id, created_by=caller.id
    ).insert()
    targets = [await make_user(superuser=True), await make_user(superuser=True)]
    for target in targets:
        if operation == "hard":
            await target.set({"is_active": False})
        auth = await headers(caller)
        if operation == "bulk":
            response = await async_client.post(
                "/api/v1/users/bulk/deactivate",
                headers=auth,
                json={"user_ids": [str(target.id)]},
            )
        else:
            prefix = "/hard" if operation == "hard" else ""
            response = await async_client.delete(
                f"/api/v1/users{prefix}/{target.id}", headers=auth
            )
        assert response.status_code == 403, response.text
        assert await User.get(target.id) is not None
        if operation != "hard":
            assert (await User.get(target.id)).is_active


@pytest.mark.asyncio
async def test_mixed_bulk_deactivate_rejects_without_partial_changes(
    async_client, policy_setup
):
    _, _, admin_role = policy_setup
    caller, ordinary, protected = (
        await make_user(),
        await make_user(),
        await make_user(superuser=True),
    )
    await UserActor(
        user_id=caller.id, actor_id=admin_role.id, created_by=caller.id
    ).insert()
    response = await async_client.post(
        "/api/v1/users/bulk/deactivate",
        headers=await headers(caller),
        json={"user_ids": [str(ordinary.id), str(protected.id)]},
    )
    assert response.status_code == 403
    assert (await User.get(ordinary.id)).is_active


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"is_superuser": True},
        {"is_superuser": None},
        {"role": "Administrator"},
        {"is_active": True},
    ],
)
async def test_candidate_cannot_write_privileges_single_or_bulk(
    async_client, policy_setup, payload
):
    candidate, _, _ = policy_setup
    response = await async_client.post(
        "/api/v1/users/bulk/update",
        headers=await headers(candidate),
        json={"user_ids": [str(candidate.id)], "update_data": payload},
    )
    assert response.status_code == 403
    response = await async_client.put(
        f"/api/v1/users/{candidate.id}", headers=await headers(candidate), json=payload
    )
    assert response.status_code == 403
    assert not (await User.get(candidate.id)).is_superuser


@pytest.mark.asyncio
async def test_candidate_cannot_assign_or_remove_global_roles(
    async_client, policy_setup
):
    candidate, _, admin_role = policy_setup
    response = await async_client.post(
        "/api/v1/user-actor/user-actors",
        headers=await headers(candidate),
        params={"user_id": str(candidate.id), "actor_id": str(admin_role.id)},
    )
    assert response.status_code == 403
    assert not await UserActor.find_one(
        {"user_id": candidate.id, "actor_id": admin_role.id}
    )
    link = await UserActor.find_one(UserActor.user_id == candidate.id)
    response = await async_client.delete(
        f"/api/v1/user-actor/user-actors/{link.id}", headers=await headers(candidate)
    )
    assert response.status_code == 403
    assert await UserActor.get(link.id)


@pytest.mark.asyncio
async def test_services_reject_privileged_calls_without_router(policy_setup):
    candidate, _, admin_role = policy_setup
    caller = CurrentUser(user=candidate, permission_names={"users:edit"})
    for call in [
        UserService.bulk_update_users(
            [str(candidate.id)], UserUpdate(is_superuser=True), caller
        ),
        UserActorService.assign_actor(str(candidate.id), str(admin_role.id), caller),
        UserActorService.delete_user_actor(str(ObjectId()), caller),
    ]:
        with pytest.raises(CustomError) as error:
            await call
        assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_administrator_cannot_promote_self_or_modify_superuser(
    async_client, policy_setup
):
    _, _, admin_role = policy_setup
    admin = await make_user()
    superuser = await make_user(superuser=True)
    await UserActor(
        user_id=admin.id, actor_id=admin_role.id, created_by=superuser.id
    ).insert()
    for target, payload in [
        (admin, {"is_superuser": True}),
        (superuser, {"full_name": "changed"}),
    ]:
        response = await async_client.put(
            f"/api/v1/users/{target.id}", headers=await headers(admin), json=payload
        )
        assert response.status_code == 403
        response = await async_client.post(
            "/api/v1/users/bulk/update",
            headers=await headers(admin),
            json={"user_ids": [str(target.id)], "update_data": payload},
        )
        assert response.status_code == 403
    response = await async_client.post(
        "/api/v1/user-actor/user-actors",
        headers=await headers(admin),
        params={"user_id": str(admin.id), "actor_id": str(admin_role.id)},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_global_rbac_writes_require_superuser_even_with_permission_grants(
    async_client, policy_setup
):
    user, _, admin_role = policy_setup
    await UserActor(
        user_id=user.id, actor_id=admin_role.id, created_by=user.id
    ).insert()
    permission = await Permission.find_one(Permission.name == "users:edit")
    response = await async_client.put(
        f"/api/v1/actors/update-actor/{admin_role.id}",
        headers=await headers(user),
        json={"name": "Renamed", "description": None},
    )
    assert response.status_code == 403
    response = await async_client.post(
        "/api/v1/actor-permissions/actor-permission",
        headers=await headers(user),
        json={"actor_id": str(admin_role.id), "permission_ids": [str(permission.id)]},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_superuser_can_promote_and_manage_role_links(
    async_client, policy_setup, monkeypatch
):
    # This unit test checks authorization; real atomicity is covered by integration tests.
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def unit_scope():
        yield

    monkeypatch.setattr("app.core.transactions.transaction_scope", unit_scope)
    target, _, admin_role = policy_setup
    superuser = await make_user(superuser=True)
    response = await async_client.post(
        "/api/v1/users/bulk/update",
        headers=await headers(superuser),
        json={"user_ids": [str(target.id)], "update_data": {"is_superuser": True}},
    )
    assert response.status_code == 200
    assert (await User.get(target.id)).is_superuser
    response = await async_client.post(
        "/api/v1/user-actor/user-actors",
        headers=await headers(superuser),
        params={"user_id": str(target.id), "actor_id": str(admin_role.id)},
    )
    assert response.status_code == 201
    link = await UserActor.find_one({"user_id": target.id, "actor_id": admin_role.id})
    assert link and link.created_by == superuser.id
    response = await async_client.delete(
        f"/api/v1/user-actor/user-actors/{link.id}", headers=await headers(superuser)
    )
    assert response.status_code == 200
    assert not await UserActor.get(link.id)


@pytest.mark.asyncio
async def test_bulk_validates_all_targets_before_writing(policy_setup):
    candidate, _, _ = policy_setup
    caller = CurrentUser(user=await make_user(superuser=True))
    with pytest.raises(CustomError):
        await UserService.bulk_update_users(
            [str(candidate.id), str(ObjectId())], UserUpdate(is_superuser=True), caller
        )
    assert not (await User.get(candidate.id)).is_superuser
    for data in [
        UserUpdate(is_superuser=False),
        UserUpdate(is_active=False),
        UserUpdate(is_superuser=None),
    ]:
        with pytest.raises(CustomError):
            await UserService.update_user(caller.user_id, data, caller)
    assert (await User.get(caller.user.id)).is_superuser


@pytest.mark.asyncio
async def test_migration_removes_only_legacy_grants_and_preserves_self_profile(
    async_client, policy_setup
):
    candidate, role, admin_role = policy_setup
    custom = await Permission(name="custom:keep", is_active=True).insert()
    await ActorPermission(actor_id=role.id, permission_id=custom.id).insert()
    await _revoke_legacy_candidate_user_permissions()
    await _ensure_default_actors()
    await _revoke_legacy_candidate_user_permissions()
    assert await DatabaseMigration.find(DatabaseMigration.version == 3).count() == 1
    for name in ["users:edit", "users:view"]:
        permission = await Permission.find_one(Permission.name == name)
        assert not await ActorPermission.find_one(
            {"actor_id": role.id, "permission_id": permission.id}
        )
        assert await ActorPermission.find_one(
            {"actor_id": admin_role.id, "permission_id": permission.id}
        )
    assert await ActorPermission.find_one(
        {"actor_id": role.id, "permission_id": custom.id}
    )
    response = await async_client.put(
        f"/api/v1/users/{candidate.id}",
        headers=await headers(candidate),
        json={"full_name": "My profile"},
    )
    assert response.status_code == 200
    assert (await User.get(candidate.id)).full_name == "My profile"


@pytest.mark.asyncio
async def test_pre_migration_authorization_cache_is_not_reused(
    async_client, policy_setup, monkeypatch
):
    candidate, _, _ = policy_setup
    await _revoke_legacy_candidate_user_permissions()
    old_key = cache.cache_key("authz", str(candidate.id))
    old_cache = {
        old_key: {"actors": [settings.ADMIN_ROLE_NAME], "permissions": ["users:edit"]}
    }
    reads = AsyncMock(side_effect=lambda key: old_cache.get(key))
    monkeypatch.setattr(cache, "get_json", reads)
    response = await async_client.post(
        "/api/v1/users/bulk/update",
        headers=await headers(candidate),
        json={"user_ids": [str(candidate.id)], "update_data": {"is_superuser": True}},
    )
    assert response.status_code == 403
    assert all(call.args[0] != old_key for call in reads.call_args_list)


@pytest.mark.asyncio
async def test_migration_checksum_mismatch_blocks_startup(policy_setup):
    await DatabaseMigration(version=3, name="unexpected", checksum="x" * 64).insert()
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        await _revoke_legacy_candidate_user_permissions()


@pytest.mark.asyncio
@pytest.mark.parametrize("revocation", ["link", "actor", "permission"])
async def test_existing_token_loses_revoked_permissions(
    async_client, policy_setup, revocation
):
    caller, _, admin_role = policy_setup
    link = await UserActor(
        user_id=caller.id, actor_id=admin_role.id, created_by=caller.id
    ).insert()
    auth = await headers(caller)
    first, second = await make_user(), await make_user()
    response = await async_client.delete(f"/api/v1/users/{first.id}", headers=auth)
    assert response.status_code == 204
    if revocation == "link":
        await link.delete()
    elif revocation == "actor":
        await admin_role.set({"is_active": False})
    else:
        permission = await Permission.find_one(Permission.name == "users:delete")
        await permission.set({"is_active": False})
    response = await async_client.delete(f"/api/v1/users/{second.id}", headers=auth)
    assert response.status_code == 403
    assert (await User.get(second.id)).is_active


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["hashed_password", "auth_version", "$set"])
async def test_bulk_rejects_untyped_internal_fields(async_client, policy_setup, field):
    target, _, _ = policy_setup
    caller = await make_user(superuser=True)
    response = await async_client.post(
        "/api/v1/users/bulk/update",
        headers=await headers(caller),
        json={"user_ids": [str(target.id)], "update_data": {field: {"is_superuser": True}}},
    )
    assert response.status_code == 422
    stored = await User.get(target.id)
    assert not stored.is_superuser
    assert stored.hashed_password == "unused"
    assert stored.auth_version == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", range(8))
@pytest.mark.parametrize("caller_state", ["candidate", "administrator", "revoked", "inactive", "deleted"])
async def test_global_rbac_services_reject_unprivileged_or_stale_caller(
    policy_setup, operation, caller_state
):
    _, role, _ = policy_setup
    user = await make_user(superuser=caller_state in {"revoked", "inactive", "deleted"})
    caller = CurrentUser(
        user=user,
        actor_names={settings.ADMIN_ROLE_NAME} if caller_state == "administrator" else set(),
        permission_names=set(CurrentUser.SUPERUSER_ONLY_PERMISSIONS),
    )
    # Mutate through the collection, preserving the already-authenticated snapshot.
    if caller_state == "deleted":
        await User.get_motor_collection().delete_one({"_id": user.id})
    elif caller_state in {"revoked", "inactive"}:
        field = "is_superuser" if caller_state == "revoked" else "is_active"
        await User.get_motor_collection().update_one({"_id": user.id}, {"$set": {field: False}})
    permission = await Permission.find_one(Permission.name == "users:edit")
    actor_count, permission_count, link_count = (
        await Actor.count(), await Permission.count(), await ActorPermission.count()
    )
    operations = [
        lambda: ActorService.create_actor(ActorCreate(name="unauthorized"), caller),
        lambda: ActorService.update_actor(str(role.id), ActorUpdate(name="unauthorized", description=None), caller),
        lambda: ActorService.delete_actor(str(role.id), caller),
        lambda: PermissionService.create_permission(PermissionCreate(name="unauthorized", description="test"), caller),
        lambda: PermissionService.update_permission(str(permission.id), PermissionUpdate(name="unauthorized", description="test"), caller),
        lambda: PermissionService.delete_permission(str(permission.id), caller),
        lambda: ActorPermissionService.assign_permissions(str(role.id), [str(permission.id)], caller),
        lambda: ActorPermissionService.unassign_permissions(str(role.id), [str(permission.id)], caller),
    ]
    with pytest.raises(CustomError) as exc:
        await operations[operation]()
    assert exc.value.status_code == 403
    assert (await Actor.count(), await Permission.count(), await ActorPermission.count()) == (
        actor_count, permission_count, link_count
    )
    assert (await Actor.get(role.id)).name == role.name
    assert (await Actor.get(role.id)).is_active
    assert (await Permission.get(permission.id)).name == permission.name
    assert (await Permission.get(permission.id)).is_active


@pytest.mark.asyncio
async def test_superuser_global_rbac_http_lifecycle(async_client, policy_setup):
    auth = await headers(await make_user(superuser=True))
    for kind in ["actor", "permission"]:
        response = await async_client.post(
            f"/api/v1/{kind}s/create-{kind}", headers=auth,
            json={"name": "rbac-review", "description": "test"},
        )
        assert response.status_code == 200, response.text
    actor = await Actor.find_one(Actor.name == "rbac-review")
    permission = await Permission.find_one(Permission.name == "rbac-review")
    response = await async_client.post(
        "/api/v1/actor-permissions/actor-permission", headers=auth,
        json={"actor_id": str(actor.id), "permission_ids": [str(permission.id)]},
    )
    assert response.status_code == 200, response.text
    assert await ActorPermission.find_one({"actor_id": actor.id, "permission_id": permission.id})
    response = await async_client.post(
        "/api/v1/actor-permissions/unassign-permission", headers=auth,
        params={"actor_id": str(actor.id)}, json=[str(permission.id)],
    )
    assert response.status_code == 200, response.text
    assert not await ActorPermission.find_one({"actor_id": actor.id, "permission_id": permission.id})
    for kind, model, identifier in [("actor", Actor, actor.id), ("permission", Permission, permission.id)]:
        response = await async_client.put(
            f"/api/v1/{kind}s/update-{kind}/{identifier}", headers=auth,
            json={"name": "rbac-updated", "description": "updated"},
        )
        assert response.status_code == 200, response.text
        assert (await model.get(identifier)).name == "rbac-updated"
        response = await async_client.delete(
            f"/api/v1/{kind}s/delete-{kind}/{identifier}", headers=auth,
        )
        assert response.status_code == 200, response.text
        assert not (await model.get(identifier)).is_active
