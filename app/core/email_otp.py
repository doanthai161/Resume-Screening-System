import os
import logging
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

try:
    import sib_api_v3_sdk
    from sib_api_v3_sdk.rest import ApiException
    BREVO_SDK_AVAILABLE = True
except ImportError:
    BREVO_SDK_AVAILABLE = False
    logger.warning("Brevo SDK not installed. Install with: pip install brevo-python")

BREVO_API_KEY = os.getenv("BREVO_API_KEY")
BREVO_SENDER_EMAIL = os.getenv("BREVO_SENDER_EMAIL")
BREVO_SENDER_NAME = os.getenv("BREVO_SENDER_NAME", "No Reply")


def initialize_brevo_client():
    """Initialize and return Brevo API client configuration"""
    if not BREVO_SDK_AVAILABLE:
        raise ImportError("Brevo SDK is not installed. Please install with: pip install brevo-python")
    
    if not BREVO_API_KEY:
        raise ValueError("BREVO_API_KEY is not set in environment variables")
    
    configuration = sib_api_v3_sdk.Configuration()
    configuration.api_key['api-key'] = BREVO_API_KEY
    
    return configuration


async def send_otp_email(
    email: str,
    otp: str,
    otp_type: str = "registration",
    full_name: Optional[str] = None,
    template_id: Optional[int] = None,
) -> bool:
    if not BREVO_SDK_AVAILABLE:
        logger.error("Brevo SDK not available")
        return False
    
    if not BREVO_API_KEY:
        logger.error("BREVO_API_KEY is not configured")
        return False
    
    if not BREVO_SENDER_EMAIL:
        logger.error("BREVO_SENDER_EMAIL is not configured")
        return False
    
    try:
        configuration = initialize_brevo_client()
        api_instance = sib_api_v3_sdk.TransactionalEmailsApi(
            sib_api_v3_sdk.ApiClient(configuration)
        )
        
        subject_map = {
            "registration": "Verify Your Email Address",
            "password_reset": "Reset Your Password",
            "login": "Your Login Code",
            "verification": "Verification Code",
            "email_change": "Confirm Your New Email",
            "transaction": "Transaction Verification",
        }
        
        greeting = f"Hello {full_name}," if full_name else "Hello,"
        
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <style>
                body {{ 
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
                    line-height: 1.6; 
                    color: #333; 
                    max-width: 600px; 
                    margin: 0 auto; 
                    padding: 20px;
                    background-color: #f9f9f9;
                }}
                .container {{
                    background: white;
                    border-radius: 12px;
                    padding: 30px;
                    box-shadow: 0 4px 6px rgba(0, 0, 0, 0.05);
                }}
                .header {{
                    text-align: center; 
                    padding: 20px 0;
                    border-bottom: 1px solid #eee;
                    margin-bottom: 25px;
                }}
                .logo {{
                    font-size: 24px;
                    font-weight: bold;
                    color: #4f46e5;
                    margin-bottom: 10px;
                }}
                .otp-container {{
                    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                    padding: 25px;
                    text-align: center;
                    margin: 25px 0;
                    border-radius: 12px;
                    color: white;
                }}
                .otp-code {{
                    font-size: 36px;
                    font-weight: bold;
                    letter-spacing: 8px;
                    margin: 20px 0;
                    font-family: monospace;
                    background: rgba(255, 255, 255, 0.1);
                    padding: 15px;
                    border-radius: 8px;
                    display: inline-block;
                }}
                .instructions {{
                    background: #f8fafc;
                    padding: 15px;
                    border-radius: 8px;
                    margin: 20px 0;
                    border-left: 4px solid #4f46e5;
                }}
                .warning {{
                    color: #dc2626;
                    background: #fef2f2;
                    padding: 15px;
                    border-radius: 8px;
                    margin: 20px 0;
                    border: 1px solid #fecaca;
                }}
                .footer {{
                    margin-top: 30px; 
                    padding-top: 20px; 
                    border-top: 1px solid #eee; 
                    text-align: center; 
                    color: #666; 
                    font-size: 13px;
                }}
                .expiry {{
                    color: #f59e0b;
                    font-weight: 500;
                    margin-top: 10px;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <div class="logo">🔐 SecureAuth</div>
                    <h2 style="margin: 10px 0; color: #374151;">Your Security Code</h2>
                </div>
                
                <p style="font-size: 16px;">{greeting}</p>
                
                <p style="font-size: 15px; color: #4b5563;">
                    Use the following One-Time Password (OTP) to complete your 
                    <strong>{otp_type.replace('_', ' ').title()}</strong>:
                </p>
                
                <div class="otp-container">
                    <div style="font-size: 14px; opacity: 0.9;">YOUR VERIFICATION CODE</div>
                    <div class="otp-code">{otp}</div>
                    <div class="expiry">⏰ Valid for 10 minutes</div>
                </div>
                
                <div class="instructions">
                    <h4 style="margin-top: 0; color: #374151;">📋 Instructions:</h4>
                    <ol style="margin: 10px 0; padding-left: 20px;">
                        <li>Enter this code in the verification field</li>
                        <li>Complete the process within 10 minutes</li>
                        <li>Do not share this code with anyone</li>
                    </ol>
                </div>
                
                <div class="warning">
                    <h4 style="margin-top: 0;">⚠️ Security Alert</h4>
                    <p style="margin: 5px 0;">
                        <strong>Never share this code</strong> with anyone, including our support team.
                        We will never ask for your OTP via phone, email, or chat.
                    </p>
                </div>
                
                <p style="font-size: 14px; color: #6b7280;">
                    If you didn't request this code, please ignore this email or contact our support team immediately.
                </p>
                
                <div class="footer">
                    <p style="margin: 5px 0;">© {os.getenv("APP_NAME", "Your Application")} {os.getenv("CURRENT_YEAR", "2024")}</p>
                    <p style="margin: 5px 0; font-size: 12px; color: #9ca3af;">
                        This is an automated message. Please do not reply to this email.
                    </p>
                    <p style="margin: 5px 0; font-size: 12px; color: #9ca3af;">
                        Need help? Contact: {os.getenv("SUPPORT_EMAIL", "support@example.com")}
                    </p>
                </div>
            </div>
        </body>
        </html>
        """
        
        text_content = f"""{greeting}

Your One-Time Password (OTP) for {otp_type.replace('_', ' ')}:

{otp}

This code is valid for 10 minutes.

INSTRUCTIONS:
1. Enter this code in the verification field
2. Complete the process within 10 minutes
3. Do not share this code with anyone

SECURITY ALERT:
Never share this code with anyone, including our support team.
We will never ask for your OTP via phone, email, or chat.

If you didn't request this code, please ignore this email.

Best regards,
{os.getenv("APP_NAME", "Your Application")} Team
"""
        
        send_smtp_email = sib_api_v3_sdk.SendSmtpEmail(
            sender=sib_api_v3_sdk.SendSmtpEmailSender(
                name=BREVO_SENDER_NAME,
                email=BREVO_SENDER_EMAIL
            ),
            to=[sib_api_v3_sdk.SendSmtpEmailTo(
                email=email,
                name=full_name or ""
            )],
            subject=subject_map.get(otp_type, "Your Security Code"),
            html_content=html_content,
            text_content=text_content,
            tags=["OTP", otp_type.upper(), "AUTOMATED"],
            params={
                "otp": otp,
                "otp_type": otp_type,
                "full_name": full_name or "",
                "company_name": BREVO_SENDER_NAME,
                "expiry_minutes": 10
            }
        )
        
        if template_id:
            send_smtp_email.template_id = template_id
            send_smtp_email.params = {
                "OTP": otp,
                "NAME": full_name or "",
                "TYPE": otp_type.replace('_', ' ').title()
            }
        
        api_response = api_instance.send_transac_email(send_smtp_email)
        
        logger.info(f"✅ OTP email sent successfully to {email}. Message ID: {api_response.message_id}")
        logger.info(f"   OTP: {otp}, Type: {otp_type}, Recipient: {full_name or 'N/A'}")
        
        return True
        
    except ApiException as e:
        error_msg = str(e)
        logger.error(f" Brevo API Exception when sending OTP to {email}: {error_msg}")
        
        if hasattr(e, 'body') and e.body:
            logger.error(f"   API Response: {e.body}")
        
        return False
        
    except Exception as e:
        logger.error(f"❌ Unexpected error sending OTP to {email}: {str(e)}", exc_info=True)
        return False


def send_welcome_email(
    email: str,
    full_name: str,
    login_url: Optional[str] = None,
) -> bool:
    if not BREVO_SDK_AVAILABLE:
        return False
    
    try:
        configuration = initialize_brevo_client()
        api_instance = sib_api_v3_sdk.TransactionalEmailsApi(
            sib_api_v3_sdk.ApiClient(configuration)
        )
        
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <body>
            <h2>Welcome to Our Platform, {full_name}!</h2>
            <p>Your account has been successfully created.</p>
            {f'<p><a href="{login_url}">Click here to log in</a></p>' if login_url else ''}
        </body>
        </html>
        """
        
        send_smtp_email = sib_api_v3_sdk.SendSmtpEmail(
            sender=sib_api_v3_sdk.SendSmtpEmailSender(
                name=BREVO_SENDER_NAME,
                email=BREVO_SENDER_EMAIL
            ),
            to=[sib_api_v3_sdk.SendSmtpEmailTo(
                email=email,
                name=full_name
            )],
            subject=f"Welcome to {BREVO_SENDER_NAME}, {full_name}!",
            html_content=html_content,
            tags=["WELCOME", "ONBOARDING"]
        )
        
        api_response = api_instance.send_transac_email(send_smtp_email)
        logger.info(f"✅ Welcome email sent to {email}")
        return True
        
    except Exception as e:
        logger.error(f"❌ Error sending welcome email: {e}")
        return False


def check_brevo_configuration() -> dict:
    config_status = {
        "sdk_available": BREVO_SDK_AVAILABLE,
        "api_key_configured": bool(BREVO_API_KEY),
        "sender_email_configured": bool(BREVO_SENDER_EMAIL),
        "sender_name": BREVO_SENDER_NAME,
        "status": "READY" if all([
            BREVO_SDK_AVAILABLE,
            BREVO_API_KEY,
            BREVO_SENDER_EMAIL
        ]) else "NOT_READY",
        "issues": []
    }
    
    if not BREVO_SDK_AVAILABLE:
        config_status["issues"].append("Brevo SDK not installed")
    if not BREVO_API_KEY:
        config_status["issues"].append("BREVO_API_KEY not set")
    if not BREVO_SENDER_EMAIL:
        config_status["issues"].append("BREVO_SENDER_EMAIL not set")
    
    return config_status