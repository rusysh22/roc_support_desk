from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("cases", "0031_add_webchat_source"),
    ]

    operations = [
        migrations.AddField(
            model_name="caserecord",
            name="client_last_read_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                verbose_name="Client Last Read At",
                help_text="Timestamp when the portal user (requester) last opened this ticket's chat.",
            ),
        ),
    ]
