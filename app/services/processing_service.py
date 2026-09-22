from datetime import timedelta
from typing import Optional

from beanie import PydanticObjectId

from app.core import cache, job_queue
from app.core.config import settings
from app.models.application_stage_event import ApplicationStageEvent
from app.models.job_application import ApplicationStage, JobApplication
from app.models.resume_file import ResumeFile
from app.models.screening_result import ScreeningDecision, ScreeningResult, ScreeningResultStatus
from app.models.screening_run import ProcessingStatus, ResumeParseRun, ScreeningRun
from app.schemas.worker import ParseOutput, ScreeningOutput
from app.utils.time import now_utc


LEASE_DURATION = timedelta(minutes=5)


class ProcessingService:
    """Worker-facing lifecycle operations with compare-and-set leases."""

    @staticmethod
    async def claim_screening(run_id: str, worker_id: str) -> Optional[ScreeningRun]:
        now = now_utc()
        result = await ScreeningRun.find_one(
            {
                "_id": PydanticObjectId(run_id),
                "status": ProcessingStatus.QUEUED.value,
                "$expr": {"$lt": ["$attempt", "$max_attempts"]},
            }
        ).update(
            {
                "$set": {
                    "status": ProcessingStatus.RUNNING.value,
                    "worker_id": worker_id,
                    "lease_expires_at": now + LEASE_DURATION,
                    "started_at": now,
                    "updated_at": now,
                },
                "$inc": {"attempt": 1},
            }
        )
        if not result or getattr(result, "modified_count", 0) != 1:
            return None
        return await ScreeningRun.get(PydanticObjectId(run_id))

    @staticmethod
    async def renew_screening_lease(run_id: str, worker_id: str) -> bool:
        result = await ScreeningRun.find_one(
            {
                "_id": PydanticObjectId(run_id),
                "status": ProcessingStatus.RUNNING.value,
                "worker_id": worker_id,
            }
        ).update(
            {
                "$set": {
                    "lease_expires_at": now_utc() + LEASE_DURATION,
                    "updated_at": now_utc(),
                }
            }
        )
        return bool(result and getattr(result, "modified_count", 0) == 1)

    @staticmethod
    async def complete_screening(
        run_id: str,
        worker_id: str,
        output: ScreeningOutput,
    ) -> Optional[ScreeningResult]:
        run = await ScreeningRun.find_one(
            {
                "_id": PydanticObjectId(run_id),
                "status": ProcessingStatus.RUNNING.value,
                "worker_id": worker_id,
            }
        )
        if not run:
            return None

        existing = await ScreeningResult.find_one(ScreeningResult.screening_run_id == run.id)
        if existing:
            result = existing
        else:
            threshold = float(run.config_snapshot.get("scorecard", {}).get("pass_threshold", 70))
            decision = ScreeningDecision.PASS if output.overall_score >= threshold else ScreeningDecision.FAIL
            result = ScreeningResult(
                company_id=run.company_id,
                application_id=run.application_id,
                screening_run_id=run.id,
                resume_file_id=run.resume_file_id,
                job_requirement_id=run.job_requirement_id,
                scorecard_id=run.scorecard_id,
                ai_model_id=run.ai_model_id,
                overall_score=output.overall_score,
                match_percentage=output.match_percentage,
                decision=decision,
                skill_score=output.skill_score,
                experience_score=output.experience_score,
                education_score=output.education_score,
                language_score=output.language_score,
                criteria_scores=output.criteria_scores,
                scorecard_snapshot=run.config_snapshot.get("scorecard", {}),
                strengths=output.strengths,
                weaknesses=output.weaknesses,
                missing_skills=output.missing_skills,
                matched_skills=output.matched_skills,
                ai_confidence=output.ai_confidence,
                status=ScreeningResultStatus.EVALUATED,
            )
            await result.insert()

        application = await JobApplication.get(run.application_id)
        if application:
            old_stage = application.current_stage
            transitioned = False
            update_filter = {"_id": application.id, "revision": application.revision}
            update_fields = {
                "latest_screening_result_id": result.id,
                "updated_at": now_utc(),
            }
            update_command: dict = {"$set": update_fields}
            if old_stage in {ApplicationStage.APPLIED, ApplicationStage.SCREENING}:
                update_filter["current_stage"] = old_stage.value
                update_fields["current_stage"] = ApplicationStage.SCREENED.value
                update_command["$inc"] = {"revision": 1}
            update_result = await JobApplication.find_one(update_filter).update(update_command)
            transitioned = bool(
                old_stage in {ApplicationStage.APPLIED, ApplicationStage.SCREENING}
                and update_result
                and getattr(update_result, "modified_count", 0) == 1
            )
            if not update_result or getattr(update_result, "modified_count", 0) != 1:
                # A recruiter changed the application concurrently. Preserve that
                # stage and attach only the immutable screening result.
                await JobApplication.find_one({"_id": application.id}).update(
                    {
                        "$set": {
                            "latest_screening_result_id": result.id,
                            "updated_at": now_utc(),
                        }
                    }
                )
            await cache.delete_pattern(cache.cache_key("application-list", str(run.company_id), "*"))
            if transitioned:
                event_key = f"screening-complete:{run.id}"
                if not await ApplicationStageEvent.find_one(
                    {"company_id": run.company_id, "idempotency_key": event_key}
                ):
                    await ApplicationStageEvent(
                        company_id=run.company_id,
                        application_id=application.id,
                        from_stage=old_stage,
                        to_stage=ApplicationStage.SCREENED,
                        changed_by=run.triggered_by,
                        reason="Screening completed",
                        idempotency_key=event_key,
                    ).insert()

        now = now_utc()
        await ScreeningRun.find_one(
            {"_id": run.id, "status": ProcessingStatus.RUNNING.value, "worker_id": worker_id}
        ).update(
            {
                "$set": {
                    "status": ProcessingStatus.COMPLETED.value,
                    "is_terminal": True,
                    "result_id": result.id,
                    "finished_at": now,
                    "lease_expires_at": None,
                    "updated_at": now,
                }
            }
        )
        return result

    @staticmethod
    async def fail_screening(run_id: str, worker_id: str, error_code: str, error_message: str) -> bool:
        run = await ScreeningRun.find_one(
            {"_id": PydanticObjectId(run_id), "status": ProcessingStatus.RUNNING.value, "worker_id": worker_id}
        )
        if not run:
            return False
        terminal = run.attempt >= run.max_attempts
        now = now_utc()
        await ScreeningRun.find_one({"_id": run.id, "worker_id": worker_id}).update(
            {
                "$set": {
                    "status": (ProcessingStatus.FAILED if terminal else ProcessingStatus.QUEUED).value,
                    "is_terminal": terminal,
                    "error_code": error_code[:100],
                    "error_message": error_message[:2000],
                    "worker_id": None,
                    "lease_expires_at": None,
                    "queue_message_id": None if not terminal else run.queue_message_id,
                    "last_enqueued_at": None if not terminal else run.last_enqueued_at,
                    "finished_at": now if terminal else None,
                    "updated_at": now,
                }
            }
        )
        if not terminal:
            message_id = await job_queue.enqueue("screening", str(run.id), str(run.company_id))
            if message_id:
                await ScreeningRun.find_one(
                    {"_id": run.id, "status": ProcessingStatus.QUEUED.value}
                ).update(
                    {
                        "$set": {
                            "queue_message_id": str(message_id),
                            "last_enqueued_at": now,
                            "updated_at": now,
                        }
                    }
                )
        return True

    @staticmethod
    async def claim_parse(run_id: str, worker_id: str) -> Optional[ResumeParseRun]:
        now = now_utc()
        result = await ResumeParseRun.find_one(
            {
                "_id": PydanticObjectId(run_id),
                "status": ProcessingStatus.QUEUED.value,
                "$expr": {"$lt": ["$attempt", "$max_attempts"]},
            }
        ).update(
            {
                "$set": {
                    "status": ProcessingStatus.RUNNING.value,
                    "worker_id": worker_id,
                    "lease_expires_at": now + LEASE_DURATION,
                    "started_at": now,
                    "updated_at": now,
                },
                "$inc": {"attempt": 1},
            }
        )
        if not result or getattr(result, "modified_count", 0) != 1:
            return None
        run = await ResumeParseRun.get(PydanticObjectId(run_id))
        if run:
            await ResumeFile.find_one(
                {"_id": run.resume_file_id, "company_id": run.company_id}
            ).update({"$set": {"status": "processing"}})
        return run

    @staticmethod
    async def renew_parse_lease(run_id: str, worker_id: str) -> bool:
        result = await ResumeParseRun.find_one(
            {
                "_id": PydanticObjectId(run_id),
                "status": ProcessingStatus.RUNNING.value,
                "worker_id": worker_id,
            }
        ).update(
            {
                "$set": {
                    "lease_expires_at": now_utc() + LEASE_DURATION,
                    "updated_at": now_utc(),
                }
            }
        )
        return bool(result and getattr(result, "modified_count", 0) == 1)

    @staticmethod
    async def complete_parse(run_id: str, worker_id: str, output: ParseOutput) -> bool:
        run = await ResumeParseRun.find_one(
            {"_id": PydanticObjectId(run_id), "status": ProcessingStatus.RUNNING.value, "worker_id": worker_id}
        )
        if not run:
            return False
        now = now_utc()
        await ResumeFile.find_one({"_id": run.resume_file_id, "company_id": run.company_id}).update(
            {
                "$set": {
                    "parsed_data": output.parsed_data.model_dump(),
                    "status": "parsed",
                    "processed_at": now,
                    "processing_errors": [],
                }
            }
        )
        await ResumeParseRun.find_one({"_id": run.id, "worker_id": worker_id}).update(
            {
                "$set": {
                    "status": ProcessingStatus.COMPLETED.value,
                    "is_terminal": True,
                    "finished_at": now,
                    "lease_expires_at": None,
                    "updated_at": now,
                }
            }
        )
        return True

    @staticmethod
    async def fail_parse(run_id: str, worker_id: str, error_code: str, error_message: str) -> bool:
        run = await ResumeParseRun.find_one(
            {"_id": PydanticObjectId(run_id), "status": ProcessingStatus.RUNNING.value, "worker_id": worker_id}
        )
        if not run:
            return False
        terminal = run.attempt >= run.max_attempts
        now = now_utc()
        await ResumeParseRun.find_one({"_id": run.id, "worker_id": worker_id}).update(
            {
                "$set": {
                    "status": (ProcessingStatus.FAILED if terminal else ProcessingStatus.QUEUED).value,
                    "is_terminal": terminal,
                    "error_code": error_code[:100],
                    "error_message": error_message[:2000],
                    "worker_id": None,
                    "lease_expires_at": None,
                    "queue_message_id": None if not terminal else run.queue_message_id,
                    "last_enqueued_at": None if not terminal else run.last_enqueued_at,
                    "finished_at": now if terminal else None,
                    "updated_at": now,
                }
            }
        )
        await ResumeFile.find_one({"_id": run.resume_file_id, "company_id": run.company_id}).update(
            {
                "$set": {"status": "error" if terminal else "pending"},
                "$push": {"processing_errors": {"$each": [error_message[:2000]], "$slice": -50}},
            }
        )
        if not terminal:
            message_id = await job_queue.enqueue("resume-parse", str(run.id), str(run.company_id))
            if message_id:
                await ResumeParseRun.find_one(
                    {"_id": run.id, "status": ProcessingStatus.QUEUED.value}
                ).update(
                    {
                        "$set": {
                            "queue_message_id": str(message_id),
                            "last_enqueued_at": now,
                            "updated_at": now,
                        }
                    }
                )
        return True

    @staticmethod
    async def recover_expired_leases() -> int:
        """Recover expired workers and re-enqueue records missed during Redis outages."""
        now = now_utc()
        recovered = 0
        for model, queue_name in ((ScreeningRun, "screening"), (ResumeParseRun, "resume-parse")):
            stale = await model.find(
                {
                    "status": ProcessingStatus.RUNNING.value,
                    "lease_expires_at": {"$lt": now},
                    "$expr": {"$lt": ["$attempt", "$max_attempts"]},
                }
            ).limit(100).to_list()
            for run in stale:
                update = await model.find_one(
                    {
                        "_id": run.id,
                        "status": ProcessingStatus.RUNNING.value,
                        "lease_expires_at": run.lease_expires_at,
                    }
                ).update(
                    {
                        "$set": {
                            "status": ProcessingStatus.QUEUED.value,
                            "worker_id": None,
                            "lease_expires_at": None,
                            "queue_message_id": None,
                            "last_enqueued_at": None,
                            "updated_at": now,
                        }
                    }
                )
                if update and getattr(update, "modified_count", 0) == 1:
                    recovered += 1
                    message_id = await job_queue.enqueue(queue_name, str(run.id), str(run.company_id))
                    if message_id:
                        await model.find_one({"_id": run.id, "status": ProcessingStatus.QUEUED.value}).update(
                            {
                                "$set": {
                                    "queue_message_id": str(message_id),
                                    "last_enqueued_at": now,
                                    "updated_at": now,
                                }
                            }
                        )

            exhausted = await model.find(
                {
                    "status": ProcessingStatus.RUNNING.value,
                    "lease_expires_at": {"$lt": now},
                    "$expr": {"$gte": ["$attempt", "$max_attempts"]},
                }
            ).limit(100).to_list()
            for run in exhausted:
                update = await model.find_one(
                    {
                        "_id": run.id,
                        "status": ProcessingStatus.RUNNING.value,
                        "lease_expires_at": run.lease_expires_at,
                    }
                ).update(
                    {
                        "$set": {
                            "status": ProcessingStatus.FAILED.value,
                            "is_terminal": True,
                            "error_code": "worker_lease_expired",
                            "error_message": "Worker lease expired after the final attempt",
                            "worker_id": None,
                            "lease_expires_at": None,
                            "finished_at": now,
                            "updated_at": now,
                        }
                    }
                )
                if update and getattr(update, "modified_count", 0) == 1:
                    recovered += 1
                    if model is ResumeParseRun:
                        await ResumeFile.find_one(
                            {"_id": run.resume_file_id, "company_id": run.company_id}
                        ).update(
                            {
                                "$set": {"status": "error"},
                                "$push": {
                                    "processing_errors": {
                                        "$each": ["Worker lease expired after the final attempt"],
                                        "$slice": -50,
                                    }
                                },
                            }
                        )

            queued = await model.find(
                {
                    "status": ProcessingStatus.QUEUED.value,
                    "$expr": {"$lt": ["$attempt", "$max_attempts"]},
                    "$or": [
                        {"last_enqueued_at": None},
                        {
                            "last_enqueued_at": {
                                "$lt": now
                                - timedelta(seconds=settings.QUEUE_REDELIVERY_SECONDS)
                            }
                        },
                    ],
                }
            ).limit(100).to_list()
            for run in queued:
                message_id = await job_queue.enqueue(queue_name, str(run.id), str(run.company_id))
                if not message_id:
                    continue
                update = await model.find_one(
                    {
                        "_id": run.id,
                        "status": ProcessingStatus.QUEUED.value,
                        "last_enqueued_at": run.last_enqueued_at,
                    }
                ).update(
                    {
                        "$set": {
                            "queue_message_id": str(message_id),
                            "last_enqueued_at": now,
                            "updated_at": now,
                        }
                    }
                )
                if update and getattr(update, "modified_count", 0) == 1:
                    recovered += 1
        return recovered
