import asyncio
import logging
import uuid
from datetime import timedelta

from app.core.cache import cache_key
from app.core.config import settings
from app.core.redis import get_redis, init_redis, is_redis_available
from app.models.audit_log import AuditLog, AuditSeverity
from app.services.processing_service import ProcessingService
from app.utils.time import now_utc

logger = logging.getLogger(__name__)


async def run_maintenance(stop_event: asyncio.Event) -> None:
    """Run bounded reconciliation with a short Redis leader lease."""
    interval = settings.MAINTENANCE_INTERVAL_SECONDS
    while not stop_event.is_set():
        try:
            if not is_redis_available():
                await init_redis()
            redis = get_redis()
            if redis:
                owner = uuid.uuid4().hex
                acquired = await redis.set(
                    cache_key("lock", "maintenance"),
                    owner,
                    ex=max(interval * 2, 30),
                    nx=True,
                )
                if acquired:
                    try:
                        recovered = await ProcessingService.recover_expired_leases()
                        if recovered:
                            logger.info("Maintenance re-queued %s processing jobs", recovered)
                        cleanup_acquired = await redis.set(
                            cache_key("lock", "audit-retention"),
                            owner,
                            ex=24 * 60 * 60,
                            nx=True,
                        )
                        if cleanup_acquired:
                            now = now_utc()
                            result = await AuditLog.find(
                                {
                                    "$or": [
                                        {
                                            "severity": {"$ne": AuditSeverity.CRITICAL.value},
                                            "created_at": {
                                                "$lt": now
                                                - timedelta(days=settings.AUDIT_LOG_RETENTION_DAYS)
                                            },
                                        },
                                        {
                                            "severity": AuditSeverity.CRITICAL.value,
                                            "created_at": {
                                                "$lt": now
                                                - timedelta(
                                                    days=settings.AUDIT_CRITICAL_RETENTION_DAYS
                                                )
                                            },
                                        },
                                    ]
                                }
                            ).delete()
                            deleted = int(getattr(result, "deleted_count", result or 0))
                            if deleted:
                                logger.info("Maintenance removed %s expired audit records", deleted)
                    finally:
                        await redis.eval(
                            """
                            if redis.call('get', KEYS[1]) == ARGV[1] then
                                return redis.call('del', KEYS[1])
                            end
                            return 0
                            """,
                            1,
                            cache_key("lock", "maintenance"),
                            owner,
                        )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Background maintenance iteration failed")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
