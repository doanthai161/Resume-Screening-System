import smtplib
from email.mime.text import MIMEText
import os
from dotenv import load_dotenv
load_dotenv()

def send_otp_email(email: str, otp: str):
    msg = MIMEText(f"Your OTP code is: {otp}")
    msg["Subject"] = "Verify your email"
    msg["From"] = os.getenv("BREVO_SENDER_EMAIL")
    msg["To"] = email

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(os.getenv("BREVO_SENDER_EMAIL"), os.getenv("BREVO_API_KEY"))
        server.send_message(msg)
