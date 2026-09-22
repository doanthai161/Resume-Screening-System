import hashlib
import hmac
import secrets

from app.core.config import settings

def generate_otp(length: int = 6) -> str:
    return "".join(secrets.choice("0123456789") for _ in range(length))


def hash_otp(email: str, otp_type: str, otp: str) -> str:
    payload = f"{email.strip().lower()}:{otp_type}:{otp}".encode("utf-8")
    secret = settings.SECRET_KEY.get_secret_value().encode("utf-8")
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


def verify_otp_hash(email: str, otp_type: str, otp: str, expected_hash: str) -> bool:
    return hmac.compare_digest(hash_otp(email, otp_type, otp), expected_hash)
