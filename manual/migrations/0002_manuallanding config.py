from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("manual", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="ManualLandingConfig",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("hero_title", models.CharField(default="Documentation & User Guide", max_length=200)),
                ("hero_subtitle", models.TextField(blank=True, default="Select an application manual to get started.")),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Landing Page Config",
            },
        ),
    ]
