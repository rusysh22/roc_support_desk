from django.db import migrations
import encrypted_model_fields.fields


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0023_add_phone_number_to_user"),
    ]

    operations = [
        migrations.AlterField(
            model_name="emailconfig",
            name="imap_password",
            field=encrypted_model_fields.fields.EncryptedCharField(
                blank=True,
                null=True,
                verbose_name="IMAP App Password",
                help_text="e.g. Gmail App Password (16 chars, no spaces)",
            ),
        ),
        migrations.AlterField(
            model_name="emailconfig",
            name="smtp_password",
            field=encrypted_model_fields.fields.EncryptedCharField(
                blank=True,
                null=True,
                verbose_name="SMTP App Password",
            ),
        ),
    ]
