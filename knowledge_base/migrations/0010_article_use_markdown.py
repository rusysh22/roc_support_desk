from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("knowledge_base", "0009_article_is_featured"),
    ]

    operations = [
        migrations.AddField(
            model_name="article",
            name="use_markdown",
            field=models.BooleanField(
                default=False,
                help_text="When enabled, content fields are stored as Markdown and rendered as formatted HTML.",
                verbose_name="Use Markdown",
            ),
        ),
    ]
