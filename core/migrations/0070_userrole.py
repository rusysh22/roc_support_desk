import uuid

import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

ROLE_ACCESS_CHOICES = [
    ("SuperAdmin", "Super Admin"),
    ("Manager", "Manager"),
    ("SupportDesk", "Support Desk"),
    ("Engineer", "Engineer"),
    ("Auditor", "Auditor"),
    ("PortalUser", "Portal User"),
]

# (code, name, base_access, permissions, sort_order, is_system, description, extra modules)
SEED_ROLES = [
    ("SuperAdmin", "Super Admin", "SuperAdmin", ["*"], 10, True, "Full administrator.", None),
    ("Manager", "Manager", "Manager", [], 20, True, "", None),
    ("SupportDesk", "Support Desk", "SupportDesk", [], 30, True, "", None),
    ("Engineer", "Engineer", "Engineer", [], 40, True, "", None),
    ("Auditor", "Auditor", "Auditor", [], 50, True, "", None),
    ("PortalUser", "Portal User", "PortalUser", [], 60, True, "", None),
]


def seed_roles(apps, schema_editor):
    UserRole = apps.get_model("core", "UserRole")
    User = apps.get_model("core", "User")
    SiteConfig = apps.get_model("core", "SiteConfig")

    for code, name, base, perms, order, system, description, _modules in SEED_ROLES:
        UserRole.objects.get_or_create(
            code=code,
            defaults={
                "name": name, "base_access": base, "permissions": perms, "sort_order": order,
                "is_system": system, "description": description,
            },
        )
    for role in UserRole.objects.filter(is_system=True):
        User.objects.filter(role_access=role.code, user_role__isnull=True).update(user_role=role)

    # Module defaults for the new roles (on top of their base tier's).
    config = SiteConfig.objects.first()
    if config is not None:
        defaults = dict(config.role_module_defaults or {}) if isinstance(config.role_module_defaults, dict) else {}
        for code, *_rest, modules in SEED_ROLES:
            if modules and code not in defaults:
                defaults[code] = modules
        config.role_module_defaults = defaults
        config.save(update_fields=["role_module_defaults"])


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0069_auditlog_announcement_action"),
    ]

    operations = [
        migrations.CreateModel(
            name="UserRole",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Created At")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Updated At")),
                ("code", models.CharField(
                    help_text="Stable key, e.g. finance_staff. Cannot be changed later.", max_length=50, unique=True,
                    validators=[django.core.validators.RegexValidator(
                        "^[A-Za-z][A-Za-z0-9_]*$", "Letters, digits and underscores; start with a letter.")],
                    verbose_name="Code",
                )),
                ("name", models.CharField(max_length=100, unique=True, verbose_name="Role Name")),
                ("description", models.TextField(blank=True, default="", verbose_name="Description")),
                ("base_access", models.CharField(
                    choices=ROLE_ACCESS_CHOICES, default="PortalUser", max_length=20, verbose_name="Base Access Level",
                    help_text="Built-in level this role behaves as in modules that do not use permissions yet "
                              "(Portal User = portal only; the others open the Support Desk).",
                )),
                ("permissions", models.JSONField(blank=True, default=list, verbose_name="Permissions")),
                ("is_system", models.BooleanField(default=False, editable=False, verbose_name="System Role")),
                ("is_active", models.BooleanField(default=True, verbose_name="Active")),
                ("sort_order", models.PositiveIntegerField(default=0, verbose_name="Display Order")),
                ("created_by", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name="%(app_label)s_%(class)s_created", to=settings.AUTH_USER_MODEL, verbose_name="Created By",
                )),
                ("updated_by", models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name="%(app_label)s_%(class)s_updated", to=settings.AUTH_USER_MODEL, verbose_name="Updated By",
                )),
            ],
            options={"verbose_name": "User Role", "verbose_name_plural": "User Roles", "ordering": ["sort_order", "name"]},
        ),
        migrations.AddField(
            model_name="user",
            name="user_role",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="users",
                to="core.userrole", verbose_name="User Role",
                help_text="Role from the User Roles master data. Sets the base access level and permissions.",
            ),
        ),
        migrations.RunPython(seed_roles, migrations.RunPython.noop),
    ]
