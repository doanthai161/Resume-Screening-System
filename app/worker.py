"""Run with python -m app.worker --queue resume-parse|screening."""
import argparse
import asyncio
import logging
import signal

from beanie import init_beanie
from motor.motor_asyncio import AsyncIOMotorClient
from redis.asyncio import Redis

from app.core import redis as redis_state
from app.core.config import settings
from app.core.database import DOCUMENT_MODELS, require_transaction_topology
from app.workers.adapters import load_adapter
from app.workers.runtime import Worker, maintain

logger = logging.getLogger(__name__)


async def serve(queue: str) -> None:
    # Validate adapter before connecting or claiming: this release has no fake inference.
    adapter = load_adapter(settings.WORKER_ADAPTER_FACTORY, queue)
    mongo = AsyncIOMotorClient(
        settings.MONGODB_URL, serverSelectionTimeoutMS=settings.MONGODB_SERVER_SELECTION_TIMEOUT,
        maxPoolSize=settings.MONGODB_MAX_POOL_SIZE,
    )
    redis = Redis.from_url(
        settings.REDIS_URL, decode_responses=True,
        max_connections=settings.REDIS_MAX_CONNECTIONS,
        socket_timeout=settings.REDIS_SOCKET_TIMEOUT,
        socket_connect_timeout=settings.REDIS_SOCKET_CONNECT_TIMEOUT,
        socket_keepalive=True, health_check_interval=30,
    )
    stop = asyncio.Event()
    tasks = []
    old_handlers = {}
    try:
        await require_transaction_topology(mongo)
        # API owns bootstrap/migrations; the worker binds ODM and ensures declared indexes.
        await init_beanie(database=mongo[settings.MONGODB_DB_NAME], document_models=DOCUMENT_MODELS)
        await redis.ping()  # Redis is mandatory in every worker environment.
        redis_state.redis_client = redis
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            old_handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))
        worker = Worker(queue, adapter)
        tasks = [asyncio.create_task(worker.run(stop)), asyncio.create_task(maintain(stop))]
        logger.info("Worker ready queue=%s id=%s", queue, worker.worker_id)
        await stop.wait()
        # Finish current work if possible; cancellation leaves pending delivery and
        # Mongo lease for recovery rather than marking an interrupted job successful.
        await asyncio.wait(tasks, timeout=settings.WORKER_SHUTDOWN_SECONDS)
    finally:
        stop.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)
        try:
            await asyncio.wait_for(adapter.aclose(), timeout=5)
        finally:
            redis_state.redis_client = None
            try:
                await redis.aclose()
            finally:
                mongo.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Resume processing worker (requires an inference adapter)")
    parser.add_argument("--queue", choices=["resume-parse", "screening"], required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(serve(args.queue))
    except Exception as exc:
        # Exception text from external adapters/connections can contain credentials.
        logger.error("Worker stopped (%s). Check adapter configuration and dependencies.", type(exc).__name__)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
