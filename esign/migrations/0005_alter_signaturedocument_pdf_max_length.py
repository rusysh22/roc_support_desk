from django.db import migrations, models
import esign.models


class Migration(migrations.Migration):

    dependencies = [
        ("esign", "0004_signaturedocument_certificate_style"),
    ]

    operations = [
        migrations.AlterField(
            model_name="signaturedocument",
            name="original_pdf",
            field=models.FileField(
                max_length=500,
                upload_to=esign.models._original_pdf_path,
                verbose_name="Original PDF",
            ),
        ),
        migrations.AlterField(
            model_name="signaturedocument",
            name="signed_pdf",
            field=models.FileField(
                blank=True,
                max_length=500,
                null=True,
                upload_to=esign.models._signed_pdf_path,
                verbose_name="Signed PDF",
            ),
        ),
    ]
