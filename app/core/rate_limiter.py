from slowapi import Limiter

from app.core.config import settings
from app.core.security import get_client_identifier


limiter = Limiter(
    key_func=get_client_identifier,
    default_limits=[settings.RATE_LIMIT_DEFAULT] if settings.RATE_LIMIT_ENABLED else [],
    storage_uri=(
        settings.REDIS_URL
        if settings.RATE_LIMIT_ENABLED
        and settings.ENVIRONMENT in {"staging", "production"}
        else "memory://"
    ),
)
