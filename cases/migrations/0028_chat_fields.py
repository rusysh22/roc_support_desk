from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("cases", "0027_casecategory_is_attachment_mandatory"),
    ]

    operations = [
        migrations.AddField(
            model_name="caserecord",
            name="guest_token",
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text="Random token that allows anonymous users to access their own ticket via chat.",
                max_length=64,
                verbose_name="Guest Token",
            ),
        ),
        migrations.AddField(
            model_name="message",
            name="is_system",
            field=models.BooleanField(
                default=False,
                help_text="Auto-generated messages: ticket created, status changed, etc.",
                verbose_name="Is System Message",
            ),
        ),
    ]
