from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0025_loginattempt_must_change_password"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AuditLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "actor",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="audit_logs",
                        to=settings.AUTH_USER_MODEL,
                        help_text="Authenticated user who triggered the event; null for anonymous actions.",
                    ),
                ),
                (
                    "actor_username",
                    models.CharField(
                        max_length=150,
                        blank=True,
                        help_text="Snapshot of login_username at event time (survives user deletion).",
                    ),
                ),
                (
                    "action",
                    models.CharField(
                        max_length=30,
                        db_index=True,
                        choices=[
                            ("LOGIN_SUCCESS", "Login — Success"),
                            ("LOGIN_FAIL", "Login — Failed"),
                            ("LOGOUT", "Logout"),
                            ("PASSWORD_CHANGE", "Password Changed"),
                            ("BULK_IMPORT", "Bulk User Import"),
                            ("EXPORT", "Data Export"),
                            ("ROLE_CHANGE", "Role Changed"),
                            ("SMTP_UPDATE", "SMTP/IMAP Config Updated"),
                            ("TICKET_CLOSE", "Ticket Closed"),
                            ("TICKET_REOPEN", "Ticket Reopened"),
                            ("LICENSE_OP", "License Operation"),
                            ("ACCOUNT_REQUEST", "Account Request Submitted"),
                        ],
                    ),
                ),
                ("ip_address", models.GenericIPAddressField(null=True, blank=True)),
                (
                    "target_type",
                    models.CharField(
                        max_length=100,
                        blank=True,
                        help_text="Model label of the affected object, e.g. 'cases.caserecord'.",
                    ),
                ),
                ("target_id", models.CharField(max_length=100, blank=True)),
                (
                    "details",
                    models.JSONField(
                        default=dict,
                        blank=True,
                        help_text="Arbitrary key-value context for the event.",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
            ],
            options={
                "verbose_name": "Audit Log",
                "verbose_name_plural": "Audit Logs",
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(fields=["action", "created_at"], name="core_auditl_action_idx"),
                    models.Index(fields=["actor", "created_at"], name="core_auditl_actor_idx"),
                ],
            },
        ),
    ]
