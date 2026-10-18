from django.db import migrations, models

import core.models


def seed_role_module_defaults(apps, schema_editor):
    SiteConfig = apps.get_model("core", "SiteConfig")
    defaults = core.models.default_role_module_defaults()
    for config in SiteConfig.objects.all():
        data = dict(defaults)
        legacy = config.portal_user_modules
        if isinstance(legacy, list) and legacy:
            data["PortalUser"] = list(legacy)
        current = config.role_module_defaults
        if isinstance(current, dict) and current:
            for role, modules in current.items():
                if isinstance(modules, list):
                    data[str(role)] = list(modules)
        config.role_module_defaults = data
        config.portal_user_modules = list(data.get("PortalUser") or core.models.default_portal_user_modules())
        config.save(update_fields=["role_module_defaults", "portal_user_modules"])


def unseed_role_module_defaults(apps, schema_editor):
    SiteConfig = apps.get_model("core", "SiteConfig")
    SiteConfig.objects.all().update(role_module_defaults={})


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0054_fix_portal_user_module_defaults"),
    ]

    operations = [
        migrations.AddField(
            model_name="siteconfig",
            name="role_module_defaults",
            field=models.JSONField(
                blank=True,
                default=core.models.default_role_module_defaults,
                help_text="Default module slugs per role. Users without a per-account override inherit the list for their role_access.",
                verbose_name="Role Module Defaults",
            ),
        ),
        migrations.AlterField(
            model_name="siteconfig",
            name="portal_user_modules",
            field=models.JSONField(
                blank=True,
                default=core.models.default_portal_user_modules,
                help_text="Legacy field — kept in sync with role_module_defaults['PortalUser']. Prefer Module Defaults (role_module_defaults).",
                verbose_name="Portal User Modules",
            ),
        ),
        migrations.RunPython(seed_role_module_defaults, unseed_role_module_defaults),
    ]
