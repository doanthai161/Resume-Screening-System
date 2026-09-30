"""One in-flight job per process; Mongo owns status, Redis owns delivery only."""
import asyncio
import logging
from uuid import uuid4

from bson import ObjectId
from pydantic import ValidationError

from app.core import job_queue
from app.core.config import settings
from app.core.errors import CustomError
from app.models.screening_run import ResumeParseRun, ScreeningRun
from app.schemas.worker import ParseOutput, ScreeningOutput
from app.services.processing_service import ProcessingService
from app.workers.adapters import WorkerAdapter
from app.workers.errors import CircuitOpenError, ParseAdapterError

logger = logging.getLogger(__name__)
GROUP = "processing-v1"


class LeaseLost(Exception):
    pass


class Worker:
    def __init__(self, queue: str, adapter: WorkerAdapter):
        if queue not in {"resume-parse", "screening"}:
            raise ValueError("Unsupported worker queue")
        self.queue = queue
        self.adapter = adapter
        self.worker_id = f"worker-{uuid4().hex}"
        self.cursor = "0-0"
        parse = queue == "resume-parse"
        self.model = ResumeParseRun if parse else ScreeningRun
        self.output_model = ParseOutput if parse else ScreeningOutput
        self.claim = ProcessingService.claim_parse if parse else ProcessingService.claim_screening
        self.renew = ProcessingService.renew_parse_lease if parse else ProcessingService.renew_screening_lease
        self.complete = ProcessingService.complete_parse if parse else ProcessingService.complete_screening
        self.fail = ProcessingService.fail_parse if parse else ProcessingService.fail_screening
        self.execute = adapter.parse if parse else adapter.screen

    async def _heartbeat(self, run) -> None:
        while True:
            await asyncio.sleep(settings.WORKER_HEARTBEAT_SECONDS)
            try:
                renewed = await self.renew(str(run.id), self.worker_id, generation=run.attempt)
            except Exception:
                raise LeaseLost() from None
            if not renewed:
                raise LeaseLost()

    async def _execute_with_lease(self, run):
        work = asyncio.create_task(self.execute(run))
        heartbeat = asyncio.create_task(self._heartbeat(run))
        try:
            done, _ = await asyncio.wait(
                {work, heartbeat}, timeout=settings.WORKER_JOB_TIMEOUT_SECONDS,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                raise TimeoutError()
            if heartbeat in done:
                await heartbeat
                raise LeaseLost()
            return self.output_model.model_validate(await work)
        finally:
            work.cancel()
            heartbeat.cancel()
            await asyncio.gather(work, heartbeat, return_exceptions=True)

    async def handle(self, message_id: str, fields: dict) -> None:
        run_id, company_id = fields.get("resource_id"), fields.get("company_id")
        if not all(isinstance(value, str) and ObjectId.is_valid(value) for value in (run_id, company_id)):
            # Do not log raw payloads: a malformed message may contain secrets/PII.
            logger.warning("Discarding malformed worker delivery")
            await job_queue.acknowledge(self.queue, GROUP, message_id)
            return
        stored = await self.model.find_one({"_id": ObjectId(run_id), "company_id": ObjectId(company_id)})
        if not stored:
            logger.warning("Discarding missing or mismatched worker delivery")
            await job_queue.acknowledge(self.queue, GROUP, message_id)
            return
        run = await self.claim(run_id, self.worker_id)
        if not run:
            # Terminal/duplicate/running jobs need no new work. Expired running
            # jobs are recovered from Mongo even if this delivery is acknowledged.
            await job_queue.acknowledge(self.queue, GROUP, message_id)
            return
        try:
            output = await self._execute_with_lease(run)
        except LeaseLost:
            logger.warning("Worker lease lost; discarding output")
            return
        except Exception as exc:
            fail_options = {"generation": run.attempt}
            if isinstance(exc, ParseAdapterError):
                code = exc.code
                fail_options["retryable"] = exc.retryable
                if self.queue == "resume-parse" and isinstance(exc, CircuitOpenError):
                    fail_options["deferred"] = True
            else:
                code = "worker_timeout" if isinstance(exc, TimeoutError) else (
                    "worker_invalid_output" if isinstance(exc, ValidationError) else "worker_adapter_error"
                )
            if await self.fail(
                run_id,
                self.worker_id,
                code,
                "Worker processing failed",
                **fail_options,
            ):
                await job_queue.acknowledge(self.queue, GROUP, message_id)
            return
        try:
            completed = await self.complete(run_id, self.worker_id, output, generation=run.attempt)
        except CustomError as exc:
            if exc.status_code != 422:
                raise
            completed = await self.fail(
                run_id, self.worker_id, "worker_invalid_output", "Worker output does not match run configuration",
                generation=run.attempt,
            )
        # Unknown commit outcomes/DB failures deliberately leave delivery pending.
        if completed:
            await job_queue.acknowledge(self.queue, GROUP, message_id)

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                self.cursor, pending = await job_queue.reclaim(self.queue, GROUP, self.worker_id, self.cursor)
                if pending:
                    deliveries = pending
                else:
                    batches = await job_queue.read(self.queue, GROUP, self.worker_id, count=1, block_ms=1000)
                    deliveries = [message for _, messages in batches for message in messages]
                for message_id, fields in deliveries:
                    if stop.is_set():
                        break
                    await self.handle(message_id, fields)
            except Exception as exc:
                logger.warning("Worker delivery deferred (%s)", type(exc).__name__)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=2)
                except TimeoutError:
                    pass


async def maintain(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await ProcessingService.recover_expired_leases()
        except Exception as exc:
            logger.warning("Worker recovery deferred (%s)", type(exc).__name__)
        try:
            await asyncio.wait_for(stop.wait(), settings.MAINTENANCE_INTERVAL_SECONDS)
        except TimeoutError:
            pass
