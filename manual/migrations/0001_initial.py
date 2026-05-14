"""Initial migration for manual app."""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Manual",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=200)),
                ("slug", models.SlugField(blank=True, max_length=220, unique=True)),
                ("description", models.TextField(blank=True)),
                ("icon", models.CharField(default="📗", help_text="Emoji icon", max_length=10)),
                ("is_published", models.BooleanField(default=False)),
                ("order", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="manuals_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Manual",
                "verbose_name_plural": "Manuals",
                "ordering": ["order", "title"],
            },
        ),
        migrations.CreateModel(
            name="ManualPage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=300)),
                ("slug", models.SlugField(blank=True, max_length=320)),
                ("content", models.JSONField(blank=True, default=dict, help_text="TipTap JSON document")),
                ("is_faq", models.BooleanField(default=False, help_text="Mark this node as FAQ")),
                ("is_published", models.BooleanField(default=False)),
                ("order", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="manual_pages_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "manual",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="pages",
                        to="manual.manual",
                    ),
                ),
                (
                    "parent",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="children",
                        to="manual.manualpage",
                    ),
                ),
            ],
            options={
                "verbose_name": "Manual Page",
                "verbose_name_plural": "Manual Pages",
                "ordering": ["order", "title"],
            },
        ),
    ]
