from django.db import migrations

import core.models


def fix_empty_module_access(apps, schema_editor):
    User = apps.get_model("core", "User")
    SiteConfig = apps.get_model("core", "SiteConfig")

    for user in User.objects.all().only("id", "module_access"):
        if user.module_access == []:
            user.module_access = None
            user.save(update_fields=["module_access"])

    default = core.models.default_portal_user_modules()
    for config in SiteConfig.objects.all():
        modules = config.portal_user_modules
        if not isinstance(modules, list) or not modules:
            config.portal_user_modules = default
            config.save(update_fields=["portal_user_modules"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0053_siteconfig_developer_contact"),
    ]

    operations = [
        migrations.RunPython(fix_empty_module_access, noop_reverse),
    ]
