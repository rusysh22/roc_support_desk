from django.db import migrations, models
import esign.models


class Migration(migrations.Migration):

    dependencies = [
        ("esign", "0006_alter_certificate_style_to_stamp_style"),
    ]

    operations = [
        migrations.AddField(
            model_name="signaturedocument",
            name="preview_pdf",
            field=models.FileField(
                blank=True,
                help_text="Intermediate signed PDF generated after each signature for in-progress viewing.",
                max_length=500,
                null=True,
                upload_to=esign.models._preview_pdf_path,
                verbose_name="Preview PDF",
            ),
        ),
    ]
