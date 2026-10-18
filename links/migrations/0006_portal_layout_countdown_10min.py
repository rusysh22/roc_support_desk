import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("links", "0005_portal_video_link_and_sound"),
    ]

    operations = [
        migrations.AddField(
            model_name="shortlink",
            name="portal_layout",
            field=models.CharField(
                choices=[("full", "Full page"), ("mini", "Mini page")], default="full", max_length=5,
                help_text="Full page: the video / image large on the left, the message and redirect on the right (desktop). Mini page: one small centred card.",
                verbose_name="Portal layout",
            ),
        ),
        migrations.AlterField(
            model_name="shortlink",
            name="portal_countdown",
            field=models.PositiveSmallIntegerField(
                default=10,
                help_text="Seconds before auto-redirect, at most 10 minutes (600). 0 = no countdown, visitor clicks Continue.",
                validators=[django.core.validators.MinValueValidator(0), django.core.validators.MaxValueValidator(600)],
                verbose_name="Countdown (seconds)",
            ),
        ),
    ]
