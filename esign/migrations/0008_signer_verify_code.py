from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("esign", "0007_signaturedocument_preview_pdf"),
    ]

    operations = [
        migrations.AddField(
            model_name="signer",
            name="verify_code",
            field=models.CharField(
                blank=True,
                help_text="One-time code e-mailed to external signers; required alongside their email to access the signing page.",
                max_length=20,
                verbose_name="Verification Code",
            ),
        ),
    ]
