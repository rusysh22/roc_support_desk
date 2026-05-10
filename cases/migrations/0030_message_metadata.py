from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("cases", "0029_alter_caserecord_source_alter_message_channel"),
    ]

    operations = [
        migrations.AddField(
            model_name="message",
            name="metadata",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="Arbitrary structured data for actionable message types (polls, approvals, etc.).",
                verbose_name="Metadata",
            ),
        ),
    ]
