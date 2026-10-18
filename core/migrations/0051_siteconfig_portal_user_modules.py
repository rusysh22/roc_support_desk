from django.db import migrations, models

import core.models


def seed_portal_user_modules(apps, schema_editor):
    SiteConfig = apps.get_model("core", "SiteConfig")
    default = list(core.models.DEFAULT_PORTAL_USER_MODULES)
    for config in SiteConfig.objects.all():
        current = config.portal_user_modules
        if not isinstance(current, list) or not current:
            config.portal_user_modules = default
            config.save(update_fields=["portal_user_modules"])


def unseed_portal_user_modules(apps, schema_editor):
    SiteConfig = apps.get_model("core", "SiteConfig")
    SiteConfig.objects.all().update(portal_user_modules=[])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0048_user_role_access_engineer"),
    ]

    operations = [
        migrations.AddField(
            model_name="siteconfig",
            name="portal_user_modules",
            field=models.JSONField(
                blank=True,
                default=core.models.default_portal_user_modules,
                help_text="Default module slugs granted to all Portal Users. Per-user overrides still take precedence when set.",
                verbose_name="Portal User Modules",
            ),
        ),
        migrations.RunPython(seed_portal_user_modules, unseed_portal_user_modules),
    ]
