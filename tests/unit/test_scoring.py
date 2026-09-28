import pytest
from pydantic import ValidationError

from app.core.scoring import calculate_screening
from app.schemas.scoring import CriterionEvaluation
from app.schemas.worker import ScreeningOutput
from app.models.screening_result import ScreeningResult
from app.schemas.recruitment import ScreeningResultResponse
from bson import ObjectId


def evaluated(score):
    return CriterionEvaluation(status="evaluated", score=score, reason="CV evidence",
                               evidence=[{"page": 1, "text": "Project work"}])


def card():
    return {"pass_threshold": 70, "criteria": [
        {"code": "python", "name": "Python", "weight": .4, "required": True, "minimum_score": 60},
        {"code": "api", "name": "API", "weight": .35},
        {"code": "db", "name": "Database", "weight": .25},
    ]}


def test_missing_criterion_is_null_with_weighted_bounds():
    result = calculate_screening(card(), {"python": evaluated(80), "api": evaluated(70)})
    assert result["overall_score"] is None
    assert result["match_percentage"] is None
    assert result["criteria_scores"]["db"] is None
    assert result["criteria_evaluations"]["db"].status == "insufficient_evidence"
    assert result["evidence_coverage"] == 75
    assert (result["score_lower_bound"], result["score_upper_bound"]) == (56.5, 81.5)
    assert result["decision"] == "hold"


@pytest.mark.parametrize("status", ["insufficient_evidence", "conflicting_evidence"])
def test_unresolved_required_criterion_cannot_pass(status):
    result = calculate_screening(card(), {
        "python": CriterionEvaluation(status=status, reason="Needs verification"),
        "api": evaluated(100), "db": evaluated(100),
    })
    assert result["decision"] == "hold"
    assert result["overall_score"] is None
    assert result["evidence_coverage"] == 60


@pytest.mark.parametrize("score,decision", [(0, "fail"), (70, "pass"), (69.999, "pass"), (69.99, "fail"), (100, "pass")])
def test_complete_weighted_score_and_threshold(score, decision):
    result = calculate_screening(card(), {key: evaluated(score) for key in ("python", "api", "db")})
    assert result["decision"] == decision
    assert result["evidence_coverage"] == 100
    assert result["overall_score"] == round(score, 2)
    assert result["score_lower_bound"] == result["score_upper_bound"]


def test_required_minimum_overrides_high_total():
    result = calculate_screening(card(), {"python": evaluated(59), "api": evaluated(100), "db": evaluated(100)})
    assert result["overall_score"] == 83.6
    assert result["decision"] == "fail"


def test_all_missing_has_no_total_and_zero_coverage():
    result = calculate_screening(card(), {})
    assert result["overall_score"] is None and result["decision"] == "hold"
    assert result["evidence_coverage"] == 0
    assert (result["score_lower_bound"], result["score_upper_bound"]) == (0, 100)


@pytest.mark.parametrize("payload", [
    {"status": "evaluated", "score": None, "evidence": [{"text": "x"}]},
    {"status": "evaluated", "score": 80},
    {"status": "insufficient_evidence", "score": 0},
    {"status": "conflicting_evidence", "score": 80},
    {"status": "evaluated", "score": float("nan"), "evidence": [{"text": "x"}]},
    {"status": "evaluated", "score": float("inf"), "evidence": [{"text": "x"}]},
    {"status": "evaluated", "score": 101, "evidence": [{"text": "x"}]},
    {"status": "parser_error"},
])
def test_invalid_evaluation_rejected(payload):
    with pytest.raises(ValidationError):
        CriterionEvaluation(reason="test", **payload)


def test_worker_cannot_supply_aggregate_or_unknown_fields():
    with pytest.raises(ValidationError):
        ScreeningOutput(criteria_evaluations={}, overall_score=100)
    with pytest.raises(ValueError):
        calculate_screening(card(), {"invented": evaluated(100)})


@pytest.mark.parametrize("snapshot", [{}, {"criteria": [], "pass_threshold": 70}, {
    "criteria": [{"code": "a", "name": "A", "weight": .2}], "pass_threshold": 70,
}])
def test_invalid_snapshot_never_falls_back_to_worker_total(snapshot):
    with pytest.raises(ValueError):
        calculate_screening(snapshot, {})


def test_legacy_result_remains_readable_without_inventing_coverage(mock_db):
    item = ScreeningResult(id=ObjectId(), resume_file_id=ObjectId(), job_requirement_id=ObjectId(),
                           overall_score=80, match_percentage=80)
    response = ScreeningResultResponse.model_validate(item)
    assert response.scoring_version == 1
    assert response.overall_score == 80
    assert response.evidence_coverage is None
    assert response.criteria_evaluations == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("access", ["allowed", "other_branch", "other_company", "no_permission"])
async def test_result_http_tenant_authorization_and_null_json(async_client, monkeypatch, access):
    from test_p1_security import user, tenant, membership
    from app.core.config import settings
    from app.core.rate_limiter import limiter
    from app.core.security import issue_token_pair
    from app.models.actor import Actor
    from app.models.permission import Permission
    from app.models.actor_permission import ActorPermission
    from app.models.user_actor import UserActor
    from app.models.company_branch import CompanyBranch
    from app.models.job_application import JobApplication
    from app.models.screening_run import ScreeningRun

    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(limiter, "enabled", False)
    monkeypatch.setattr("app.core.security.get_redis", lambda: None)
    owner, caller = await user(), await user()
    company, branch = await tenant(owner)
    role = await Actor(name="Test recruiter", is_active=True).insert()
    await UserActor(user_id=caller.id, actor_id=role.id, created_by=owner.id).insert()
    if access != "no_permission":
        permission = await Permission(name="screening_runs:view", is_active=True).insert()
        await ActorPermission(actor_id=role.id, permission_id=permission.id).insert()
    if access == "other_company":
        _, caller_branch = await tenant(caller)
    elif access == "other_branch":
        caller_branch = await CompanyBranch(
            company_id=company.id, bussiness_type="IT", branch_name="Other",
            address="Other", company_size=1, working_days=[], created_by=owner.id,
        ).insert()
    else:
        caller_branch = branch
    await membership(caller, caller_branch)
    application = await JobApplication(
        company_id=company.id, company_branch_id=branch.id,
        candidate_id=ObjectId(), resume_file_id=ObjectId(),
        job_requirement_id=ObjectId(), applied_by=owner.id,
    ).insert()
    run = await ScreeningRun(
        company_id=company.id, application_id=application.id,
        resume_file_id=application.resume_file_id, job_requirement_id=application.job_requirement_id,
        scorecard_id=ObjectId(), ai_model_id=ObjectId(), triggered_by=owner.id,
        idempotency_key="test-result-read", input_hash="a" * 64,
    ).insert()
    await ScreeningResult(
        company_id=company.id, application_id=application.id, screening_run_id=run.id,
        resume_file_id=application.resume_file_id, job_requirement_id=application.job_requirement_id,
        scorecard_snapshot=card(), **calculate_screening(card(), {"python": evaluated(80), "api": evaluated(70)}),
    ).insert()
    pair = await issue_token_pair(caller)
    response = await async_client.get(
        f"/api/v1/recruitment/screenings/{run.id}/result",
        params={"company_id": str(company.id)},
        headers={"Authorization": f"Bearer {pair.access_token}"},
    )
    assert response.status_code == (200 if access == "allowed" else 403), response.text
    if access == "allowed":
        data = response.json()["data"]
        assert data["overall_score"] is None and data["decision"] == "hold"
        assert data["criteria_evaluations"]["db"]["score"] is None
        assert data["evidence_coverage"] == 75
        assert data["score_upper_bound"] == 81.5
        assert "file_path" not in data and "model_config" not in data
