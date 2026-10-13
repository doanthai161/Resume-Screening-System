"""Build inference requests from durable snapshots without consulting live models."""
from app.core.scoring import ScoringSnapshot
from app.core.screening_evidence import validate_screening_source
from app.schemas.screening_http import (
    ScreeningHTTPScorecard, ScreeningModelSpec, ScreeningServiceRequest,
)


def build_screening_request(run) -> ScreeningServiceRequest:
    try:
        source = validate_screening_source(run.source_snapshot)
        model = ScreeningModelSpec.model_validate(run.config_snapshot.get("model"))
        if model.ai_model_id != run.ai_model_id:
            raise ValueError("Model snapshot does not belong to run")
        stored_card = run.config_snapshot["scorecard"]
        card = ScoringSnapshot.model_validate(stored_card)
        if str(stored_card["id"]) != str(run.scorecard_id):
            raise ValueError("Scorecard snapshot does not belong to run")
        for field, expected in (("company_id", run.company_id), ("job_requirement_id", run.job_requirement_id)):
            if field in stored_card and str(stored_card[field]) != str(expected):
                raise ValueError("Scorecard snapshot does not belong to resource")
        return ScreeningServiceRequest(
            run_id=run.id, company_id=run.company_id, application_id=run.application_id,
            resume_file_id=run.resume_file_id, input_hash=run.input_hash, source=source,
            scorecard=ScreeningHTTPScorecard(
                scorecard_id=run.scorecard_id, version=stored_card["version"],
                criteria=card.criteria, pass_threshold=card.pass_threshold,
            ), model=model, prompt_version=run.config_snapshot.get("screening_prompt_version"),
        )
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ValueError("Invalid or missing frozen screening HTTP input") from None
