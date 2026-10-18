from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0051_siteconfig_portal_user_modules"),
    ]

    operations = [
        migrations.AddField(
            model_name="siteconfig",
            name="developer_contact_email",
            field=models.EmailField(
                blank=True,
                default="",
                help_text="Email shown under Help & About → Contact Developer.",
                max_length=254,
                verbose_name="Developer Contact Email",
            ),
        ),
        migrations.AddField(
            model_name="siteconfig",
            name="developer_contact_name",
            field=models.CharField(
                blank=True,
                default="IT Support",
                help_text="Display name shown under Help & About → Contact Developer.",
                max_length=100,
                verbose_name="Developer Contact Name",
            ),
        ),
    ]
