from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("knowledge_base", "0008_add_article_attachment"),
    ]

    operations = [
        migrations.AddField(
            model_name="article",
            name="is_featured",
            field=models.BooleanField(
                db_index=True,
                default=False,
                help_text="Pin to Featured Articles section (published only, max 10).",
                verbose_name="Featured",
            ),
        ),
    ]
