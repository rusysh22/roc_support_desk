"""
RoC Desk — Celery Application
===============================
Auto-discovers tasks from all registered Django apps.
"""
import os
import ssl

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "roc_desk.settings")

app = Celery("roc_desk")
app.config_from_object("django.conf:settings", namespace="CELERY")

# TLS Redis URLs (e.g. managed Redis services) use the rediss:// scheme, which celery's
# redis transport/backend refuses to connect to without an explicit
# ssl_cert_reqs — otherwise it raises "A rediss:// URL must have parameter
# ssl_cert_reqs ..." at worker startup. Plain redis:// (local Docker Desktop)
# is untouched since neither string starts with "rediss://".
if str(app.conf.broker_url).startswith("rediss://"):
    app.conf.broker_use_ssl = {"ssl_cert_reqs": ssl.CERT_NONE}
if str(app.conf.result_backend).startswith("rediss://"):
    app.conf.redis_backend_use_ssl = {"ssl_cert_reqs": ssl.CERT_NONE}

app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Health-check task for verifying Celery connectivity."""
    print(f"Request: {self.request!r}")

# -----------------------------------------------------------------
# Celery Task Routing — isolate chat tasks from heavy system jobs
# -----------------------------------------------------------------
app.conf.task_routes = {
    "cases.send_chat_offline_notification": {"queue": "chat_tasks"},
    "cases.batch_mark_messages_read": {"queue": "chat_tasks"},
    "cases.auto_close_idle_tickets": {"queue": "chat_tasks"},
}

# -----------------------------------------------------------------
# Celery Beat Schedule
# -----------------------------------------------------------------
from celery.schedules import crontab  # noqa: E402
from django.conf import settings as django_settings  # noqa: E402

_verify_interval_hours = django_settings.LICENSE_SETTINGS.get('VERIFY_INTERVAL_HOURS', 24)

app.conf.beat_schedule = {
    "poll-imap-emails-every-1-minute": {
        "task": "gateways.poll_imap_emails_task",
        "schedule": 60.0,
    },
    "auto-close-idle-tickets-nightly": {
        "task": "cases.auto_close_idle_tickets",
        "schedule": crontab(hour=2, minute=0),  # 02:00 server time daily
    },
    "verify-license-periodic": {
        "task": "licensing.verify_license_periodic",
        "schedule": _verify_interval_hours * 3600,
    },
}
