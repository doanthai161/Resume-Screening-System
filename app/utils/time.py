from datetime import datetime, timedelta, timezone

def now_utc():
    return datetime.now(timezone.utc)
def is_expired_check(expires_at: datetime) -> bool:
    if expires_at.tzinfo is None:
        expires_at_utc = expires_at.replace(tzinfo=timezone.utc)
    else:
        expires_at_utc = expires_at.astimezone(timezone.utc)
    
    return datetime.now(timezone.utc) > expires_at_utc
def now_vn():
    VN_TZ = timezone(timedelta(hours=7))
    return datetime.now(VN_TZ)

def add_minutes_utc(minutes: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=minutes)

def ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)