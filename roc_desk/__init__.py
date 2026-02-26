"""
RoC Desk — Package Init
=========================
Ensures Celery app is loaded when Django starts.
"""
from .celery import app as celery_app

__all__ = ("celery_app",)
