from datetime import datetime, timedelta, timezone

def now_utc():
    return datetime.now(timezone.utc)

def now_vn():
    VN_TZ = timezone(timedelta(hours=7))
    return datetime.now(VN_TZ)

def add_minutes_utc(minutes: int) -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=minutes)