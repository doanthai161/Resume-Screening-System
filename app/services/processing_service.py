from app.core.scoring import calculate_screening
from app.core.transactions import current_session, transactional
from datetime import timedelta
from typing import Optional

from beanie import PydanticObjectId

from app.core import cache, job_queue
from app.core.errors import CustomError, ErrorCodes
from app.core.config import settings
from app.models.application_stage_event import ApplicationStageEvent
from app.models.job_application import ApplicationStage, JobApplication
from app.models.resume_file import ResumeFile
from app.models.screening_result import (
    ScreeningResult,
    ScreeningResultStatus,
)
from app.models.screening_run import ProcessingStatus, ResumeParseRun, ScreeningRun
from app.schemas.worker import ParseOutput, ScreeningOutput
from app.utils.time import now_utc


LEASE_DURATION = timedelta(minutes=5)


class ProcessingService:
    """Worker-facing lifecycle operations with compare-and-set leases."""

    @staticmethod
    @transactional
    async def claim_screening(run_id: str, worker_id: str) -> Optional[ScreeningRun]:
        now = now_utc()
        result = await ScreeningRun.find_one(
            {
                "_id": PydanticObjectId(run_id),
                "status": ProcessingStatus.QUEUED.value,
                "$expr": {"$lt": ["$attempt", "$max_attempts"]},
            },
            session=current_session(),
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
            },
            session=current_session(),
        )
        if not result or getattr(result, "modified_count", 0) != 1:
            return None
        return await ScreeningRun.get(
            PydanticObjectId(run_id), session=current_session()
        )

    @staticmethod
    async def renew_screening_lease(
        run_id: str, worker_id: str, *, generation: int
    ) -> bool:
        result = await ScreeningRun.find_one(
            {
                "_id": PydanticObjectId(run_id),
                "status": ProcessingStatus.RUNNING.value,
                "worker_id": worker_id,
                "attempt": generation,
                "lease_expires_at": {"$gt": now_utc()},
            },
            session=current_session(),
        ).update(
            {
                "$set": {
                    "lease_expires_at": now_utc() + LEASE_DURATION,
                    "updated_at": now_utc(),
                }
            },
            session=current_session(),
        )
        return bool(result and getattr(result, "modified_count", 0) == 1)

    @staticmethod
    @transactional
    async def complete_screening(
        run_id: str,
        worker_id: str,
        output: ScreeningOutput,
        *,
        generation: int,
    ) -> Optional[ScreeningResult]:
        run = await ScreeningRun.find_one(
            {
                "_id": PydanticObjectId(run_id),
                "status": ProcessingStatus.RUNNING.value,
                "worker_id": worker_id,
                "attempt": generation,
                "lease_expires_at": {"$gt": now_utc()},
            },
            session=current_session(),
        )
        if not run:
            return None

        existing = await ScreeningResult.find_one(
            ScreeningResult.screening_run_id == run.id, session=current_session()
        )
        if existing:
            result = existing
        else:
            try:
                assessment = calculate_screening(
                    run.config_snapshot.get("scorecard", {}), output.criteria_evaluations
                )
            except ValueError as exc:
                raise CustomError(ErrorCodes.VALIDATION, "Invalid scoring output or scorecard snapshot", 422) from exc
            result = ScreeningResult(
                company_id=run.company_id,
                application_id=run.application_id,
                screening_run_id=run.id,
                resume_file_id=run.resume_file_id,
                job_requirement_id=run.job_requirement_id,
                scorecard_id=run.scorecard_id,
                ai_model_id=run.ai_model_id,
                **assessment,
                scorecard_snapshot=run.config_snapshot.get("scorecard", {}),
                strengths=output.strengths,
                weaknesses=output.weaknesses,
                missing_skills=output.missing_skills,
                matched_skills=output.matched_skills,
                ai_confidence=output.ai_confidence,
                status=ScreeningResultStatus.EVALUATED,
            )
            await result.insert(session=current_session())

        application = await JobApplication.get(
            run.application_id, session=current_session()
        )
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
            update_result = await JobApplication.find_one(
                update_filter, session=current_session()
            ).update(update_command, session=current_session())
            transitioned = bool(
                old_stage in {ApplicationStage.APPLIED, ApplicationStage.SCREENING}
                and update_result
                and getattr(update_result, "modified_count", 0) == 1
            )
            if not update_result or getattr(update_result, "modified_count", 0) != 1:
                # A recruiter changed the application concurrently. Preserve that
                # stage and attach only the immutable screening result.
                await JobApplication.find_one(
                    {"_id": application.id}, session=current_session()
                ).update(
                    {
                        "$set": {
                            "latest_screening_result_id": result.id,
                            "updated_at": now_utc(),
                        }
                    },
                    session=current_session(),
                )
            await cache.delete_pattern(
                cache.cache_key("application-list", str(run.company_id), "*")
            )
            if transitioned:
                event_key = f"screening-complete:{run.id}"
                if not await ApplicationStageEvent.find_one(
                    {"company_id": run.company_id, "idempotency_key": event_key},
                    session=current_session(),
                ):
                    await ApplicationStageEvent(
                        company_id=run.company_id,
                        application_id=application.id,
                        from_stage=old_stage,
                        to_stage=ApplicationStage.SCREENED,
                        changed_by=run.triggered_by,
                        reason="Screening completed",
                        idempotency_key=event_key,
                    ).insert(session=current_session())

        now = now_utc()
        terminal_update = await ScreeningRun.find_one(
            {
                "_id": run.id,
                "status": ProcessingStatus.RUNNING.value,
                "worker_id": worker_id,
                "attempt": generation,
                "lease_expires_at": {"$gt": now_utc()},
            },
            session=current_session(),
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
            },
            session=current_session(),
        )
        if not terminal_update or terminal_update.modified_count != 1:
            raise CustomError(ErrorCodes.CONFLICT, "Worker lease lost", 409)
        return result

    @staticmethod
    @transactional
    async def fail_screening(
        run_id: str,
        worker_id: str,
        error_code: str,
        error_message: str,
        *,
        generation: int,
    ) -> bool:
        run = await ScreeningRun.find_one(
            {
                "_id": PydanticObjectId(run_id),
                "status": ProcessingStatus.RUNNING.value,
                "worker_id": worker_id,
                "attempt": generation,
                "lease_expires_at": {"$gt": now_utc()},
            },
            session=current_session(),
        )
        if not run:
            return False
        terminal = run.attempt >= run.max_attempts
        now = now_utc()
        terminal_update = await ScreeningRun.find_one(
            {
                "_id": run.id,
                "worker_id": worker_id,
                "attempt": generation,
                "lease_expires_at": {"$gt": now_utc()},
                "status": ProcessingStatus.RUNNING.value,
            },
            session=current_session(),
        ).update(
            {
                "$set": {
                    "status": (
                        ProcessingStatus.FAILED if terminal else ProcessingStatus.QUEUED
                    ).value,
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
            },
            session=current_session(),
        )
        if not terminal_update or terminal_update.modified_count != 1:
            raise CustomError(ErrorCodes.CONFLICT, "Worker lease lost", 409)
        if not terminal:
            message_id = await job_queue.enqueue(
                "screening", str(run.id), str(run.company_id)
            )
            if message_id:
                await ScreeningRun.find_one(
                    {"_id": run.id, "status": ProcessingStatus.QUEUED.value},
                    session=current_session(),
                ).update(
                    {
                        "$set": {
                            "queue_message_id": str(message_id),
                            "last_enqueued_at": now,
                            "updated_at": now,
                        }
                    },
                    session=current_session(),
                )
        return True

    @staticmethod
    @transactional
    async def claim_parse(run_id: str, worker_id: str) -> Optional[ResumeParseRun]:
        now = now_utc()
        result = await ResumeParseRun.find_one(
            {
                "_id": PydanticObjectId(run_id),
                "status": ProcessingStatus.QUEUED.value,
                "$expr": {"$lt": ["$attempt", "$max_attempts"]},
            },
            session=current_session(),
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
            },
            session=current_session(),
        )
        if not result or getattr(result, "modified_count", 0) != 1:
            return None
        run = await ResumeParseRun.get(
            PydanticObjectId(run_id), session=current_session()
        )
        if run:
            await ResumeFile.find_one(
                {"_id": run.resume_file_id, "company_id": run.company_id},
                session=current_session(),
            ).update({"$set": {"status": "processing"}}, session=current_session())
        return run

    @staticmethod
    async def renew_parse_lease(
        run_id: str, worker_id: str, *, generation: int
    ) -> bool:
        result = await ResumeParseRun.find_one(
            {
                "_id": PydanticObjectId(run_id),
                "status": ProcessingStatus.RUNNING.value,
                "worker_id": worker_id,
                "attempt": generation,
                "lease_expires_at": {"$gt": now_utc()},
            },
            session=current_session(),
        ).update(
            {
                "$set": {
                    "lease_expires_at": now_utc() + LEASE_DURATION,
                    "updated_at": now_utc(),
                }
            },
            session=current_session(),
        )
        return bool(result and getattr(result, "modified_count", 0) == 1)

    @staticmethod
    @transactional
    async def complete_parse(
        run_id: str, worker_id: str, output: ParseOutput, *, generation: int
    ) -> bool:
        run = await ResumeParseRun.find_one(
            {
                "_id": PydanticObjectId(run_id),
                "status": ProcessingStatus.RUNNING.value,
                "worker_id": worker_id,
                "attempt": generation,
                "lease_expires_at": {"$gt": now_utc()},
            },
            session=current_session(),
        )
        if not run:
            return False
        now = now_utc()
        await ResumeFile.find_one(
            {"_id": run.resume_file_id, "company_id": run.company_id},
            session=current_session(),
        ).update(
            {
                "$set": {
                    "parsed_data": output.parsed_data.model_dump(),
                    "status": "parsed",
                    "processed_at": now,
                    "processing_errors": [],
                }
            },
            session=current_session(),
        )
        terminal_update = await ResumeParseRun.find_one(
            {
                "_id": run.id,
                "worker_id": worker_id,
                "attempt": generation,
                "lease_expires_at": {"$gt": now_utc()},
                "status": ProcessingStatus.RUNNING.value,
            },
            session=current_session(),
        ).update(
            {
                "$set": {
                    "status": ProcessingStatus.COMPLETED.value,
                    "is_terminal": True,
                    "finished_at": now,
                    "lease_expires_at": None,
                    "updated_at": now,
                }
            },
            session=current_session(),
        )
        if not terminal_update or terminal_update.modified_count != 1:
            raise CustomError(ErrorCodes.CONFLICT, "Worker lease lost", 409)
        return True

    @staticmethod
    @transactional
    async def fail_parse(
        run_id: str,
        worker_id: str,
        error_code: str,
        error_message: str,
        *,
        generation: int,
    ) -> bool:
        run = await ResumeParseRun.find_one(
            {
                "_id": PydanticObjectId(run_id),
                "status": ProcessingStatus.RUNNING.value,
                "worker_id": worker_id,
                "attempt": generation,
                "lease_expires_at": {"$gt": now_utc()},
            },
            session=current_session(),
        )
        if not run:
            return False
        terminal = run.attempt >= run.max_attempts
        now = now_utc()
        terminal_update = await ResumeParseRun.find_one(
            {
                "_id": run.id,
                "worker_id": worker_id,
                "attempt": generation,
                "lease_expires_at": {"$gt": now_utc()},
                "status": ProcessingStatus.RUNNING.value,
            },
            session=current_session(),
        ).update(
            {
                "$set": {
                    "status": (
                        ProcessingStatus.FAILED if terminal else ProcessingStatus.QUEUED
                    ).value,
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
            },
            session=current_session(),
        )
        if not terminal_update or terminal_update.modified_count != 1:
            raise CustomError(ErrorCodes.CONFLICT, "Worker lease lost", 409)
        await ResumeFile.find_one(
            {"_id": run.resume_file_id, "company_id": run.company_id},
            session=current_session(),
        ).update(
            {
                "$set": {"status": "error" if terminal else "pending"},
                "$push": {
                    "processing_errors": {
                        "$each": [error_message[:2000]],
                        "$slice": -50,
                    }
                },
            },
            session=current_session(),
        )
        if not terminal:
            message_id = await job_queue.enqueue(
                "resume-parse", str(run.id), str(run.company_id)
            )
            if message_id:
                await ResumeParseRun.find_one(
                    {"_id": run.id, "status": ProcessingStatus.QUEUED.value},
                    session=current_session(),
                ).update(
                    {
                        "$set": {
                            "queue_message_id": str(message_id),
                            "last_enqueued_at": now,
                            "updated_at": now,
                        }
                    },
                    session=current_session(),
                )
        return True

    @staticmethod
    @transactional
    async def recover_expired_leases() -> int:
        """Recover expired workers and re-enqueue records missed during Redis outages."""
        now = now_utc()
        recovered = 0
        for model, queue_name in (
            (ScreeningRun, "screening"),
            (ResumeParseRun, "resume-parse"),
        ):
            stale = (
                await model.find(
                    {
                        "status": ProcessingStatus.RUNNING.value,
                        "lease_expires_at": {"$lt": now},
                        "$expr": {"$lt": ["$attempt", "$max_attempts"]},
                    },
                    session=current_session(),
                )
                .limit(100)
                .to_list()
            )
            for run in stale:
                update = await model.find_one(
                    {
                        "_id": run.id,
                        "status": ProcessingStatus.RUNNING.value,
                        "lease_expires_at": run.lease_expires_at,
                    },
                    session=current_session(),
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
                    },
                    session=current_session(),
                )
                if update and getattr(update, "modified_count", 0) == 1:
                    recovered += 1
                    message_id = await job_queue.enqueue(
                        queue_name, str(run.id), str(run.company_id)
                    )
                    if message_id:
                        await model.find_one(
                            {"_id": run.id, "status": ProcessingStatus.QUEUED.value},
                            session=current_session(),
                        ).update(
                            {
                                "$set": {
                                    "queue_message_id": str(message_id),
                                    "last_enqueued_at": now,
                                    "updated_at": now,
                                }
                            },
                            session=current_session(),
                        )

            exhausted = (
                await model.find(
                    {
                        "status": ProcessingStatus.RUNNING.value,
                        "lease_expires_at": {"$lt": now},
                        "$expr": {"$gte": ["$attempt", "$max_attempts"]},
                    },
                    session=current_session(),
                )
                .limit(100)
                .to_list()
            )
            for run in exhausted:
                update = await model.find_one(
                    {
                        "_id": run.id,
                        "status": ProcessingStatus.RUNNING.value,
                        "lease_expires_at": run.lease_expires_at,
                    },
                    session=current_session(),
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
                    },
                    session=current_session(),
                )
                if update and getattr(update, "modified_count", 0) == 1:
                    recovered += 1
                    if model is ResumeParseRun:
                        await ResumeFile.find_one(
                            {"_id": run.resume_file_id, "company_id": run.company_id},
                            session=current_session(),
                        ).update(
                            {
                                "$set": {"status": "error"},
                                "$push": {
                                    "processing_errors": {
                                        "$each": [
                                            "Worker lease expired after the final attempt"
                                        ],
                                        "$slice": -50,
                                    }
                                },
                            },
                            session=current_session(),
                        )

            queued = (
                await model.find(
                    {
                        "status": ProcessingStatus.QUEUED.value,
                        "$expr": {"$lt": ["$attempt", "$max_attempts"]},
                        "$or": [
                            {"last_enqueued_at": None},
                            {
                                "last_enqueued_at": {
                                    "$lt": now
                                    - timedelta(
                                        seconds=settings.QUEUE_REDELIVERY_SECONDS
                                    )
                                }
                            },
                        ],
                    },
                    session=current_session(),
                )
                .limit(100)
                .to_list()
            )
            for run in queued:
                message_id = await job_queue.enqueue(
                    queue_name, str(run.id), str(run.company_id)
                )
                if not message_id:
                    continue
                update = await model.find_one(
                    {
                        "_id": run.id,
                        "status": ProcessingStatus.QUEUED.value,
                        "last_enqueued_at": run.last_enqueued_at,
                    },
                    session=current_session(),
                ).update(
                    {
                        "$set": {
                            "queue_message_id": str(message_id),
                            "last_enqueued_at": now,
                            "updated_at": now,
                        }
                    },
                    session=current_session(),
                )
                if update and getattr(update, "modified_count", 0) == 1:
                    recovered += 1
        return recovered
