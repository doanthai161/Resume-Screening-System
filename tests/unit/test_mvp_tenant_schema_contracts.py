from datetime import timedelta
import json
from unittest.mock import AsyncMock
from uuid import uuid4

from bson import ObjectId
import pytest
from pydantic import ValidationError

from app.core.errors import CustomError
from app.models.company import Company
from app.models.company_branch import CompanyBranch
from app.models.user import User
from app.models.user_company import UserCompany
from app.repositories.company_repository import CompanyRepository
from app.repositories.company_branch_repository import CompanyBranchRepository
from app.schemas.company import CompanyUpdate
from app.services.company_service import CompanyService
from app.utils.time import now_utc


class MemoryRedis:
    def __init__(self):
        self.values = {}

    async def get(self, key):
        return self.values.get(key)

    async def setex(self, key, ttl, value):
        self.values[key] = value

    async def delete(self, *keys):
        for key in keys:
            self.values.pop(key, None)


@pytest.fixture
def cached(monkeypatch):
    redis = MemoryRedis()
    for module in ("app.repositories.company_repository", "app.repositories.company_branch_repository"):
        monkeypatch.setattr(module + ".get_redis", lambda: redis)
        monkeypatch.setattr(module + ".is_redis_available", lambda: True)
    monkeypatch.setattr("app.core.cache.get_redis", lambda: None)
    return redis


async def data():
    key = uuid4().hex
    owner = await User(email=key + "@example.com", username=key, phone_number="10000000001", hashed_password="unused").insert()
    member_key = uuid4().hex
    member = await User(email=member_key + "@example.com", username=member_key, phone_number="10000000002", hashed_password="unused").insert()
    company = await Company(user_id=owner.id, name=key, company_short_name=key, company_code=key,
                            email=owner.email, website="https://example.com").insert()
    branch = await CompanyBranch(company_id=company.id, branch_name="Main", bussiness_type="IT",
                                 address="Synthetic", company_size=1, working_days=[], created_by=owner.id).insert()
    return owner, member, company, branch


def test_company_update_is_partial_and_optional_fields_can_be_cleared():
    assert CompanyUpdate(name="Updated").model_dump(exclude_unset=True) == {"name": "Updated"}
    assert CompanyUpdate(description=None).model_dump(exclude_unset=True) == {"description": None}


@pytest.mark.parametrize("field", ["name", "company_short_name", "company_code", "email", "website"])
def test_company_update_never_clears_required_fields(field):
    with pytest.raises(ValidationError):
        CompanyUpdate.model_validate({field: None})


@pytest.mark.parametrize("field", ["user_id", "is_active", "is_superuser", "role"])
def test_company_update_rejects_internal_fields(field):
    with pytest.raises(ValidationError):
        CompanyUpdate.model_validate({field: "forbidden"})


@pytest.mark.asyncio
async def test_company_and_branch_caches_roundtrip_real_model_types(cached):
    _, _, company, branch = await data()
    cold = await CompanyBranchRepository.get_company_branch(str(branch.id))
    hot = await CompanyBranchRepository.get_company_branch(str(branch.id))
    assert isinstance(cold, CompanyBranch) and isinstance(hot, CompanyBranch)
    assert hot.id == branch.id and hot.company_id == company.id and hot.created_by == branch.created_by
    assert isinstance(hot.company_id, ObjectId)
    assert isinstance(json.loads(cached.values[CompanyBranchRepository._get_branch_key(str(branch.id))]), dict)
    first = await CompanyBranchRepository.get_company_branches(str(company.id))
    again = await CompanyBranchRepository.get_company_branches(str(company.id))
    assert [item.id for item in first] == [item.id for item in again] == [branch.id]
    assert isinstance(again[0], CompanyBranch)
    assert (await CompanyRepository.get_company_branch(str(branch.id))).id == branch.id
    assert (await CompanyRepository.get_company_branch(str(branch.id))).company_id == company.id
    assert (await CompanyRepository.get_company(str(company.id))).id == company.id
    assert (await CompanyRepository.get_company(str(company.id))).user_id == company.user_id


@pytest.mark.asyncio
async def test_old_repr_cache_is_evicted_and_rebuilt(cached):
    _, _, company, branch = await data()
    key = CompanyBranchRepository._get_company_branches_key(str(company.id))
    cached.values[key] = json.dumps(["legacy document repr"])
    records = await CompanyBranchRepository.get_company_branches(str(company.id))
    assert [item.id for item in records] == [branch.id]
    assert isinstance(json.loads(cached.values[key])[0], dict)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["expired", "future", "revoked", "branch_inactive"])
async def test_company_reads_ignore_cached_authorization_and_expired_membership(monkeypatch, state):
    owner, member, company, branch = await data()
    values = {"expired": {"end_date": now_utc() - timedelta(seconds=1)},
              "future": {"start_date": now_utc() + timedelta(days=1)},
              "revoked": {"is_active": False}, "branch_inactive": {}}
    await UserCompany(user_id=member.id, company_branch_id=branch.id, role="member",
                      assigned_by=owner.id, **values[state]).insert()
    if state == "branch_inactive":
        await branch.set({"is_active": False})
    cached = AsyncMock(return_value=[company.model_dump()])
    monkeypatch.setattr(CompanyRepository, "_get_from_cache", cached)
    records, total = await CompanyService.list_companies(1, 10, str(member.id), True)
    assert records == [] and total == 0
    with pytest.raises(CustomError) as caught:
        await CompanyService.get_company(str(company.id), str(member.id), True, ["admin"])
    assert caught.value.status_code == 403
    cached.assert_not_awaited()
