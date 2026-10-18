import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("cases", "0055_alter_ticketlocation_options"),
    ]

    operations = [
        migrations.CreateModel(
            name="FavoriteService",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("category", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE, related_name="favorited_by", to="cases.casecategory",
                )),
                ("user", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE, related_name="favorite_services",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                "verbose_name": "Favorite Service", "verbose_name_plural": "Favorite Services", "ordering": ["-created_at"],
                "constraints": [models.UniqueConstraint(fields=("user", "category"), name="cases_favorite_service_unique")],
            },
        ),
    ]
