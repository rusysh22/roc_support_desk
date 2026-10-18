from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("links", "0006_portal_layout_countdown_10min"),
    ]

    operations = [
        migrations.AddField(
            model_name="shortlink",
            name="portal_media_has_audio",
            field=models.BooleanField(
                blank=True, default=None, editable=False, null=True,
                help_text="Checked when the video is uploaded; None = unknown.",
                verbose_name="Uploaded video has a sound track",
            ),
        ),
    ]
