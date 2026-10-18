from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("knowledge_base", "0014_article_contributors_comments_off"),
    ]

    operations = [
        migrations.CreateModel(
            name="ArticleChangeLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("action", models.CharField(choices=[("created", "Created"), ("edited", "Edited"), ("submitted", "Submitted for review"), ("approved", "Approved & published"), ("rejected", "Rejected"), ("unpublished", "Unpublished"), ("featured", "Added to featured"), ("unfeatured", "Removed from featured")], max_length=20, verbose_name="Action")),
                ("actor_name", models.CharField(blank=True, default="", max_length=150, verbose_name="By (name)")),
                ("detail", models.TextField(blank=True, default="", verbose_name="Detail")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="When")),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL, verbose_name="By")),
                ("article", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="change_logs", to="knowledge_base.article", verbose_name="Article")),
            ],
            options={
                "verbose_name": "Article Change Log",
                "verbose_name_plural": "Article Change Log",
                "ordering": ["-created_at", "-id"],
            },
        ),
    ]
