"""Cases app configuration."""
from django.apps import AppConfig


class CasesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "cases"
    verbose_name = "Tickets"

    def ready(self):
        from . import signals  # noqa: F401  (Teams activity notifications)
