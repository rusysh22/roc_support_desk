"""
Core App — Celery Tasks
==========================
"""
import logging
from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.utils.html import strip_tags

logger = logging.getLogger(__name__)

@shared_task(
    name="core.send_password_reset_otp_task",
    max_retries=3,
    default_retry_delay=30,
)
def send_password_reset_otp_task(user_email: str, otp_code: str, username: str) -> str:
    """
    Sends a 6-digit OTP code to the requested user's email for password reset.
    """
    from core.models import SiteConfig
    
    site_config = SiteConfig.get_solo()
    site_name = getattr(site_config, 'site_name', 'Support Desk')

    subject = f"[{site_name}] Password Reset Verification Code"
    
    # Modern, clean HTML template for the email
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; line-height: 1.6; color: #333; }}
            .container {{ max-w: 600px; margin: 0 auto; padding: 20px; }}
            .header {{ background-color: #f8fafc; padding: 20px; border-radius: 8px 8px 0 0; border-bottom: 2px solid #e2e8f0; text-align: center; }}
            .body {{ background-color: #ffffff; padding: 30px 20px; border-left: 1px solid #e2e8f0; border-right: 1px solid #e2e8f0; border-bottom: 1px solid #e2e8f0; border-radius: 0 0 8px 8px; }}
            .otp-box {{ background-color: #f1f5f9; border: 1px dashed #cbd5e1; padding: 15px; text-align: center; margin: 25px 0; border-radius: 6px; }}
            .otp-code {{ font-size: 32px; font-weight: bold; letter-spacing: 5px; color: #4338ca; }}
            .footer {{ margin-top: 30px; font-size: 12px; color: #64748b; text-align: center; }}
        </style>
    </head>
    <body style="background-color: #f1f5f9; padding: 20px;">
        <div class="container">
            <div class="header">
                <h2 style="margin: 0; color: #1e293b;">{site_name}</h2>
            </div>
            <div class="body">
                <p>Hello <strong>{username}</strong>,</p>
                <p>We received a request to reset the password for your {site_name} account. Please use the following One-Time Password (OTP) to proceed with resetting your password:</p>
                
                <div class="otp-box">
                    <div class="otp-code">{otp_code}</div>
                </div>
                
                <p><em>Note: This code will expire in 15 minutes. If you did not request a password reset, please ignore this email or contact your administrator immediately.</em></p>
                
                <p>Best regards,<br>The {site_name} Team</p>
            </div>
            <div class="footer">
                &copy; 2026 {site_name}. All rights reserved.<br>
                This is an automated message, please do not reply.
            </div>
        </div>
    </body>
    </html>
    """
    
    # Generate plain text alternative
    text_content = strip_tags(html_content).replace('    ', '').replace('\n\n\n', '\n\n')

    try:
        from core.models import EmailConfig
        email_config = EmailConfig.get_solo()
        from_email = email_config.default_from_email or getattr(settings, "DEFAULT_FROM_EMAIL", f"noreply@{site_name.replace(' ', '').lower()}.com")
        
        email = EmailMultiAlternatives(
            subject=subject,
            body=text_content,
            from_email=from_email,
            to=[user_email],
        )
        email.attach_alternative(html_content, "text/html")
        email.send()
        
        logger.info("Password reset OTP sent to %s", user_email)
        return f"sent_to:{user_email}"
        
    except Exception as exc:
        logger.exception("Failed to send OTP email to %s: %s", user_email, exc)
        try:
            # Using self.retry in @shared_task requires declaring bind=True, but we'll let Celery catch raising it inside a standard task
            raise exc
        except Exception:
            pass
        return f"error:{user_email}"
