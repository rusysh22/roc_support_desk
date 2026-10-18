from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("knowledge_base", "0010_article_use_markdown"),
    ]

    operations = [
        TrigramExtension(),
    ]
