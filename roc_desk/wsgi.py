"""
WSGI config for RoC Desk project.
"""
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "roc_desk.settings")
application = get_wsgi_application()
