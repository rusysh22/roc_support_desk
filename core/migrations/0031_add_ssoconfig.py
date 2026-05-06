"""Migration: Add SSOConfig singleton model for Microsoft and Google SSO."""
import uuid

import django.db.models.deletion
import encrypted_model_fields.fields
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0030_add_teams_and_notification_config"),
        ("cases", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="SSOConfig",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                ("sso_enabled", models.BooleanField(default=False, verbose_name="Enable SSO")),
                (
                    "microsoft_enabled",
                    models.BooleanField(default=False, verbose_name="Enable Microsoft SSO"),
                ),
                (
                    "microsoft_client_id",
                    models.CharField(
                        blank=True,
                        max_length=200,
                        verbose_name="Microsoft Client ID (App ID)",
                    ),
                ),
                (
                    "microsoft_client_secret",
                    encrypted_model_fields.fields.EncryptedCharField(
                        blank=True,
                        default="",
                        verbose_name="Microsoft Client Secret",
                    ),
                ),
                (
                    "microsoft_tenant_id",
                    models.CharField(
                        blank=True,
                        default="organizations",
                        max_length=200,
                        verbose_name="Microsoft Tenant ID",
                    ),
                ),
                (
                    "google_enabled",
                    models.BooleanField(default=False, verbose_name="Enable Google SSO"),
                ),
                (
                    "google_client_id",
                    models.CharField(
                        blank=True,
                        max_length=200,
                        verbose_name="Google Client ID",
                    ),
                ),
                (
                    "google_client_secret",
                    encrypted_model_fields.fields.EncryptedCharField(
                        blank=True,
                        default="",
                        verbose_name="Google Client Secret",
                    ),
                ),
                (
                    "google_allowed_domains",
                    models.CharField(
                        blank=True,
                        max_length=500,
                        verbose_name="Google Allowed Domains",
                    ),
                ),
                (
                    "sso_whitelist_category",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to="cases.casecategory",
                        verbose_name="Kategori Tiket Whitelist SSO",
                    ),
                ),
            ],
            options={
                "verbose_name": "SSO Configuration",
                "verbose_name_plural": "SSO Configuration",
            },
        ),
    ]
