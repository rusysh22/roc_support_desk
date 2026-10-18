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
    bind=True,
    name="core.send_password_reset_otp_task",
    max_retries=3,
    default_retry_delay=30,
    acks_late=True,
)
def send_password_reset_otp_task(self, user_email: str, otp_code: str, username: str) -> str:
    """
    Sends a 6-digit OTP code to the requested user's email for password reset.
    """
    from core.models import SiteConfig
    
    site_config = SiteConfig.get_solo()
    site_name = getattr(site_config, 'site_name', 'Support Desk')

    subject = f"[{site_name}] Password Reset Verification Code"

    from core.utils.email_branding import attach_logo, render_branded_from_template

    html_content = render_branded_from_template(
        "emails/bodies/otp.html",
        {
            "username": username,
            "site_name": site_name,
            "otp_code": otp_code,
        },
        site_name=site_name,
        title="Password reset verification",
        footer_note="Automated security message. Do not reply to this email.",
    )
    
    text_content = strip_tags(html_content).replace('    ', '').replace('\n\n\n', '\n\n')

    try:
        from core.models import EmailConfig
        email_config = EmailConfig.get_solo()
        from_email = email_config.default_from_email or getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@rocdesk.local")

        logger.info(
            "OTP email config — host=%s, port=%s, user=%s, tls=%s, ssl=%s, from=%s",
            email_config.smtp_host,
            email_config.smtp_port,
            email_config.smtp_user,
            email_config.smtp_use_tls,
            email_config.smtp_use_ssl,
            from_email,
        )

        from django.core.mail import get_connection
        connection = get_connection(
            host=email_config.smtp_host,
            port=email_config.smtp_port,
            username=email_config.smtp_user,
            password=email_config.smtp_password,
            use_tls=email_config.smtp_use_tls,
            use_ssl=email_config.smtp_use_ssl,
            timeout=30,
        )

        email = EmailMultiAlternatives(
            subject=subject,
            body=text_content,
            from_email=from_email,
            to=[user_email],
            connection=connection,
        )
        email.attach_alternative(html_content, "text/html")
        attach_logo(email)
        email.send(fail_silently=False)

        logger.info("Password reset OTP sent to %s", user_email)
        return f"sent_to:{user_email}"

    except Exception as exc:
        logger.exception("Failed to send OTP email to %s: %s", user_email, exc)
        raise self.retry(exc=exc)
