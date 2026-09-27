"""Mongo transactions for multi-document workflow commands, without hidden retries."""
import logging
from contextlib import asynccontextmanager
from contextvars import ContextVar
from functools import wraps

from pymongo.errors import OperationFailure
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from app.core.errors import CustomError, ErrorCodes

_session = ContextVar("mongo_transaction", default=None)
_callbacks = ContextVar("after_commit", default=None)
logger = logging.getLogger(__name__)


def current_session():
    return _session.get()


def defer_after_commit(callback) -> bool:
    callbacks = _callbacks.get()
    if callbacks is None:
        return False
    callbacks.append(callback)
    return True


@asynccontextmanager
async def transaction_scope():
    from app.models.user import User

    client = User.get_motor_collection().database.client
    async with await client.start_session() as session:
        async with session.start_transaction(
            read_concern=ReadConcern("snapshot"), write_concern=WriteConcern("majority")
        ):
            token = _session.set(session)
            try:
                yield
            finally:
                _session.reset(token)


def transactional(function):
    @wraps(function)
    async def wrapped(*args, **kwargs):
        if current_session() is not None:
            return await function(*args, **kwargs)
        callbacks = []
        token = _callbacks.set(callbacks)
        try:
            try:
                async with transaction_scope():
                    result = await function(*args, **kwargs)
            except OperationFailure as exc:
                if exc.code in {11000, 112, 251} or exc.has_error_label(
                    "TransientTransactionError"
                ):
                    raise CustomError(
                        ErrorCodes.CONFLICT,
                        "Concurrent change; retry with the same idempotency key",
                        409,
                    ) from exc
                raise
        finally:
            _callbacks.reset(token)
        for callback in callbacks:
            try:
                await callback()
            except Exception:
                # The DB commit is authoritative; never report a rollback here.
                logger.exception("Post-commit cache maintenance failed")
        return result

    return wrapped
