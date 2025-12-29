from datetime import datetime, timedelta, timezone

VN_TZ = timezone(timedelta(hours=7))


def now_vn():
    return datetime.now(VN_TZ)


def to_vn_time(dt):
    if dt is None:
        return None
    return dt + timedelta(hours=7)
