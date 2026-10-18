import encrypted_model_fields.fields
from django.db import migrations, models


def enable_teams_for_existing_prefs(apps, schema_editor):
    # The profile page used to force teams_enabled=False on every save (Teams
    # wasn't wired up), so existing False values were never a real opt-out.
    UserNotificationPreference = apps.get_model("core", "UserNotificationPreference")
    UserNotificationPreference.objects.filter(teams_enabled=False).update(teams_enabled=True)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0067_notificationconfig_teams_event_toggles"),
    ]

    operations = [
        migrations.AddField(
            model_name="teamsconfig",
            name="personal_webhook_url",
            field=encrypted_model_fields.fields.EncryptedCharField(
                blank=True, null=True, max_length=1000,
                help_text="Workflow: 'When a Teams webhook request is received' → 'Post card in a chat or channel' "
                          "(Flow bot → Chat with Flow bot, recipient from the request)",
                verbose_name="Personal Notifications Workflow URL",
            ),
        ),
        migrations.AddField(
            model_name="teamsconfig",
            name="is_personal_enabled",
            field=models.BooleanField(default=False, verbose_name="Enable Personal Teams Notifications"),
        ),
        migrations.AddField(
            model_name="teamsconfig",
            name="personal_allowed_domains",
            field=models.CharField(
                blank=True, default="", max_length=500,
                help_text="Comma-separated, e.g. company.com. Only users with these email domains get personal "
                          "Teams notifications (others aren't in your Teams tenant). Blank = everyone.",
                verbose_name="Microsoft 365 Email Domains",
            ),
        ),
        migrations.AlterField(
            model_name="usernotificationpreference",
            name="teams_enabled",
            field=models.BooleanField(
                default=True,
                help_text="Receive personal notifications in Microsoft Teams (chat with the Workflows bot).",
                verbose_name="Teams Notifications",
            ),
        ),
        migrations.RunPython(enable_teams_for_existing_prefs, migrations.RunPython.noop),
    ]
