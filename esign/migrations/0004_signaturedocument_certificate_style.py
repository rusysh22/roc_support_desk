from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("esign", "0003_signer_stamp_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="signaturedocument",
            name="certificate_style",
            field=models.CharField(
                choices=[
                    ("none", "None (no certificate)"),
                    ("page", "Full Certificate Page"),
                    ("compact", "Compact Summary"),
                    ("logo_stamp", "Logo Stamp"),
                ],
                default="none",
                help_text="Choose whether and how to append a signing certificate to the final PDF.",
                max_length=20,
                verbose_name="Certificate Style",
            ),
        ),
    ]
