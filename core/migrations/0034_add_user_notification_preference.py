"""
Migration: Add UserNotificationPreference model.

Per-user notification preferences for Email, WhatsApp, and Teams channels
with event type toggles and quiet hours support.
"""
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0033_add_user_timezone"),
    ]

    operations = [
        migrations.CreateModel(
            name="UserNotificationPreference",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                # Channel toggles
                (
                    "email_enabled",
                    models.BooleanField(
                        default=True,
                        help_text="Receive notifications via email.",
                        verbose_name="Email Notifications",
                    ),
                ),
                (
                    "whatsapp_enabled",
                    models.BooleanField(
                        default=False,
                        help_text="Receive notifications via WhatsApp.",
                        verbose_name="WhatsApp Notifications",
                    ),
                ),
                (
                    "teams_enabled",
                    models.BooleanField(
                        default=False,
                        help_text="Receive notifications via Microsoft Teams webhook.",
                        verbose_name="Teams Notifications",
                    ),
                ),
                # Contact overrides
                (
                    "whatsapp_number",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="E.164 format, e.g. +6281234567890. If blank, uses phone_number from profile.",
                        max_length=20,
                        verbose_name="WhatsApp Number",
                    ),
                ),
                (
                    "teams_webhook_url",
                    models.URLField(
                        blank=True,
                        default="",
                        help_text="Personal Incoming Webhook URL from your Teams channel.",
                        max_length=1000,
                        verbose_name="Teams Webhook URL",
                    ),
                ),
                # Event toggles
                (
                    "on_new_message",
                    models.BooleanField(
                        default=True,
                        help_text="Someone replies in a ticket you own or follow.",
                        verbose_name="New chat message",
                    ),
                ),
                (
                    "on_mention",
                    models.BooleanField(
                        default=True,
                        help_text="Someone @mentions you in a chat.",
                        verbose_name="@Mention",
                    ),
                ),
                (
                    "on_status_change",
                    models.BooleanField(
                        default=True,
                        help_text="A ticket you own or follow changes status.",
                        verbose_name="Status change",
                    ),
                ),
                (
                    "on_follower_added",
                    models.BooleanField(
                        default=True,
                        help_text="You are added as a follower to a ticket.",
                        verbose_name="Added as follower",
                    ),
                ),
                # Quiet hours
                (
                    "quiet_start",
                    models.TimeField(
                        blank=True,
                        help_text="Notifications are paused from this time. e.g. 22:00",
                        null=True,
                        verbose_name="Quiet hours start",
                    ),
                ),
                (
                    "quiet_end",
                    models.TimeField(
                        blank=True,
                        help_text="Notifications resume at this time. e.g. 07:00",
                        null=True,
                        verbose_name="Quiet hours end",
                    ),
                ),
                # User FK
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="notification_pref",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="User",
                    ),
                ),
            ],
            options={
                "verbose_name": "User Notification Preference",
                "verbose_name_plural": "User Notification Preferences",
            },
        ),
    ]
