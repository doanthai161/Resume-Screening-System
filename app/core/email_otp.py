import smtplib
from email.mime.text import MIMEText
import os
from dotenv import load_dotenv
from app.logs.logging_config import logger

load_dotenv()

def send_otp_email(
    email: str,
    otp: str,
    otp_type: str = "registration",
    full_name: str | None = None,
):
    greeting = f"Hello {full_name}," if full_name else "Hello,"

    msg = MIMEText(
        f"""{greeting}

Your OTP code is: {otp}

This code will expire in a few minutes.
If you did not request this, please ignore this email.
"""
    )

    subject_map = {
        "registration": "Verify your email",
        "password_reset": "Reset your password",
    }

    msg["Subject"] = subject_map.get(otp_type, "Your OTP code")
    msg["From"] = os.getenv("BREVO_SENDER_EMAIL")
    msg["To"] = email

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(
                os.getenv("BREVO_SENDER_EMAIL"),
                os.getenv("BREVO_API_KEY"),
            )
            server.send_message(msg)
    except Exception as e:
        logger.error(
            f"Send OTP email failed for {email}: {e}",
            exc_info=True,
        )