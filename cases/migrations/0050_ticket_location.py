import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("cases", "0049_caserecord_first_response_at"),
    ]

    operations = [
        migrations.CreateModel(
            name="TicketLocation",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Created At")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Updated At")),
                ("name", models.CharField(max_length=100, unique=True, verbose_name="Location Name")),
                ("slug", models.SlugField(max_length=50, unique=True, verbose_name="Slug")),
                ("requires_detail", models.BooleanField(default=False, help_text="If checked, the requester must type the location when this option is selected.", verbose_name="Requires Custom Location")),
                ("is_active", models.BooleanField(default=True, verbose_name="Active")),
                ("order", models.PositiveIntegerField(default=0, verbose_name="Display Order")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="%(app_label)s_%(class)s_created", to="core.user", verbose_name="Created By")),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="%(app_label)s_%(class)s_updated", to="core.user", verbose_name="Updated By")),
            ],
            options={
                "verbose_name": "Ticket Location",
                "verbose_name_plural": "Ticket Locations",
                "ordering": ["order", "name"],
            },
        ),
        migrations.AddField(
            model_name="caserecord",
            name="location",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="cases", to="cases.ticketlocation", verbose_name="Location"),
        ),
        migrations.AddField(
            model_name="caserecord",
            name="location_detail",
            field=models.CharField(blank=True, help_text="Required when the selected location requires a custom value.", max_length=255, verbose_name="Location Detail"),
        ),
    ]
