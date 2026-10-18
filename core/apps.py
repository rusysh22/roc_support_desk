"""Core app configuration."""
from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
    verbose_name = "Core"

    def ready(self):
        import core.signals  # noqa: F401
        self._load_sso_providers()

    def _load_sso_providers(self):
        """
        Populate SOCIALACCOUNT_PROVIDERS from SSOConfig at startup.
        Credentials live in settings (APP), not SocialApp rows — avoid configuring both.
        """
        try:
            from django.db import connection

            tables = connection.introspection.table_names()
            if "core_ssoconfig" not in tables:
                return

            from core.models import SSOConfig

            SSOConfig.get_solo()._sync_providers_to_settings()
        except Exception:
            pass
