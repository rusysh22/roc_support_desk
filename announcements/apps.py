from django.apps import AppConfig


class AnnouncementsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "announcements"
    verbose_name = "Announcements"

    def ready(self):
        from . import signals  # noqa: F401
