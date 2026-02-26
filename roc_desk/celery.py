"""
RoC Desk — Celery Application
===============================
Auto-discovers tasks from all registered Django apps.
"""
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "roc_desk.settings")

app = Celery("roc_desk")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Health-check task for verifying Celery connectivity."""
    print(f"Request: {self.request!r}")

# -----------------------------------------------------------------
# Celery Beat Schedule
# -----------------------------------------------------------------
app.conf.beat_schedule = {
    "poll-imap-emails-every-1-minute": {
        "task": "gateways.poll_imap_emails_task",
        "schedule": 60.0,  # every 60 seconds
    },
}
