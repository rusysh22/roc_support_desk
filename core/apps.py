"""Core app configuration."""
from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
    verbose_name = "Core"

    def ready(self):
        self._load_sso_providers()

    def _load_sso_providers(self):
        """
        Populate SOCIALACCOUNT_PROVIDERS from SSOConfig at startup.
        This reads Microsoft tenant ID and Google allowed domains from the DB
        so that allauth picks them up correctly per-request.
        Credentials (client_id/secret) are synced to allauth's SocialApp model
        by SSOConfig.sync_to_social_apps(), which is called from the admin save.
        """
        try:
            from django.conf import settings
            from django.db import connection

            # Only run if migrations have been applied (table exists)
            tables = connection.introspection.table_names()
            if "core_ssoconfig" not in tables:
                return

            from core.models import SSOConfig

            config = SSOConfig.get_solo()
            providers = {}

            if config.microsoft_enabled and config.microsoft_client_id:
                providers["microsoft"] = {
                    "TENANT": config.microsoft_tenant_id or "organizations",
                    "APP": {
                        "client_id": config.microsoft_client_id,
                        "secret": config.microsoft_client_secret or "",
                        "key": "",
                    },
                }

            if config.google_enabled and config.google_client_id:
                google_cfg = {
                    "APP": {
                        "client_id": config.google_client_id,
                        "secret": config.google_client_secret or "",
                        "key": "",
                    },
                    "SCOPE": ["profile", "email"],
                    "AUTH_PARAMS": {"access_type": "online"},
                }
                if config.google_allowed_domains:
                    domains = [
                        d.strip()
                        for d in config.google_allowed_domains.split(",")
                        if d.strip()
                    ]
                    if domains:
                        google_cfg["HOSTED_DOMAIN"] = domains[0]
                providers["google"] = google_cfg

            settings.SOCIALACCOUNT_PROVIDERS = providers

        except Exception:
            pass
