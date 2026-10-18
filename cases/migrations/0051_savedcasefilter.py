import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("cases", "0050_ticket_location"),
    ]

    operations = [
        migrations.CreateModel(
            name="SavedCaseFilter",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Created At")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Updated At")),
                ("name", models.CharField(help_text="Short label for this saved filter, e.g. 'My P1 tickets'.", max_length=100, verbose_name="Name")),
                ("query_string", models.TextField(help_text="URL-encoded filter parameters captured from the ticket list page.", verbose_name="Query String")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="%(app_label)s_%(class)s_created", to="core.user", verbose_name="Created By")),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="%(app_label)s_%(class)s_updated", to="core.user", verbose_name="Updated By")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="saved_case_filters", to=settings.AUTH_USER_MODEL, verbose_name="User")),
            ],
            options={
                "verbose_name": "Saved Case Filter",
                "verbose_name_plural": "Saved Case Filters",
                "ordering": ["-created_at"],
            },
        ),
    ]
