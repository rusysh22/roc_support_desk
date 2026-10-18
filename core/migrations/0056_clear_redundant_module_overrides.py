from django.db import migrations

import core.models


def clear_redundant_module_overrides(apps, schema_editor):
    User = apps.get_model("core", "User")
    SiteConfig = apps.get_model("core", "SiteConfig")

    config = SiteConfig.objects.first()
    role_defaults = {}
    if config and isinstance(config.role_module_defaults, dict) and config.role_module_defaults:
        role_defaults = {
            role: sorted(mods) if isinstance(mods, list) else []
            for role, mods in config.role_module_defaults.items()
        }
    else:
        role_defaults = {
            role: sorted(mods)
            for role, mods in core.models.ROLE_DEFAULT_MODULES.items()
        }
        if config:
            legacy = config.portal_user_modules
            if isinstance(legacy, list) and legacy:
                role_defaults["PortalUser"] = sorted(legacy)
            config.role_module_defaults = {
                role: list(mods) for role, mods in role_defaults.items()
            }
            config.save(update_fields=["role_module_defaults"])

    for user in User.objects.exclude(module_access=None).only("id", "role_access", "module_access"):
        if not isinstance(user.module_access, list):
            continue
        current = sorted(str(m).strip() for m in user.module_access if str(m).strip())
        builtin = role_defaults.get(user.role_access) or sorted(
            core.models.ROLE_DEFAULT_MODULES.get(user.role_access, set())
        )
        hardcoded = sorted(core.models.ROLE_DEFAULT_MODULES.get(user.role_access, set()))
        if current == builtin or current == hardcoded:
            user.module_access = None
            user.save(update_fields=["module_access"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0055_siteconfig_role_module_defaults"),
    ]

    operations = [
        migrations.RunPython(clear_redundant_module_overrides, noop_reverse),
    ]
