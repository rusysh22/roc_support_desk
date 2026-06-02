from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("esign", "0002_mobiledrawsession_alter_signer_signature_image_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="signer",
            name="stamp_timestamp",
            field=models.BooleanField(default=True, verbose_name="Include Timestamp on PDF"),
        ),
        migrations.AddField(
            model_name="signer",
            name="stamp_name",
            field=models.BooleanField(default=True, verbose_name="Include Name on PDF"),
        ),
        migrations.AddField(
            model_name="signer",
            name="stamp_job_role",
            field=models.BooleanField(default=False, verbose_name="Include Job Role on PDF"),
        ),
        migrations.AddField(
            model_name="signer",
            name="stamp_job_role_text",
            field=models.CharField(
                blank=True,
                max_length=150,
                verbose_name="Job Role Text",
                help_text="The job role/title the signer chose to include on the PDF.",
            ),
        ),
    ]
