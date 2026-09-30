from time import monotonic

from app.models.resume_parse_attempt import ParseAttemptStatus, ResumeParseAttempt
from app.models.screening_run import ResumeParseRun
from app.utils.time import now_utc
from app.workers.quality import ParseQuality


class ParseAttemptRecorder:
    async def start(
        self,
        run: ResumeParseRun,
        *,
        sequence: int,
        provider: str,
        mode: str | None,
        tier: str | None,
        page_count: int,
    ) -> tuple[ResumeParseAttempt, float]:
        attempt = ResumeParseAttempt(
            run_id=run.id,
            company_id=run.company_id,
            run_attempt=run.attempt,
            sequence=sequence,
            provider=provider,
            mode=mode,
            tier=tier,
            page_count=page_count,
        )
        await attempt.insert()
        return attempt, monotonic()

    async def finish(
        self,
        attempt: ResumeParseAttempt,
        started: float,
        *,
        status: ParseAttemptStatus,
        quality: ParseQuality | None = None,
        error_code: str | None = None,
    ) -> None:
        values = {
            "status": status.value,
            "duration_ms": max(0, int((monotonic() - started) * 1000)),
            "finished_at": now_utc(),
            "error_code": error_code,
        }
        if quality is not None:
            values.update(
                {
                    "output_characters": quality.character_count,
                    "quality_score": quality.score,
                    "quality_reason": quality.reason,
                }
            )
        await ResumeParseAttempt.find_one({"_id": attempt.id}).update(
            {"$set": values}
        )
