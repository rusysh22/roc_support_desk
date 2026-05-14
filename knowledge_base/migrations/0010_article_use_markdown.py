from django.db import migrations, models


def add_use_markdown_safe(apps, schema_editor):
    """Add use_markdown column only if it doesn't already exist."""
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'knowledge_base_article' AND column_name = 'use_markdown'
        """)
        if not cursor.fetchone():
            cursor.execute(
                "ALTER TABLE knowledge_base_article "
                "ADD COLUMN use_markdown BOOLEAN NOT NULL DEFAULT FALSE"
            )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("knowledge_base", "0009_article_is_featured"),
    ]

    operations = [
        migrations.RunPython(add_use_markdown_safe, noop),
        migrations.AlterField(
            model_name="article",
            name="use_markdown",
            field=models.BooleanField(
                default=False,
                help_text="When enabled, content fields are stored as Markdown and rendered as formatted HTML.",
                verbose_name="Use Markdown",
            ),
        ),
    ]
