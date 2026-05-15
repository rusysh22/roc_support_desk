from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("manual", "0002_manuallanding_config"),
    ]

    operations = [
        migrations.AddField(
            model_name="manuallandingconfig",
            name="hero_image",
            field=models.ImageField(
                blank=True,
                null=True,
                upload_to="manual/hero/",
                help_text="Optional background image for the landing page hero section.",
            ),
        ),
    ]
