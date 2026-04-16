import uuid
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('knowledge_base', '0005_alter_article_problem_summary_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ArticleComment',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('body', models.CharField(max_length=250, verbose_name='Comment')),
                ('article', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='comments',
                    to='knowledge_base.article',
                    verbose_name='Article',
                )),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='kb_comments',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='User',
                )),
                ('created_by', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='%(class)s_created',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Created By',
                )),
                ('updated_by', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='%(class)s_updated',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Updated By',
                )),
            ],
            options={
                'verbose_name': 'Article Comment',
                'verbose_name_plural': 'Article Comments',
                'ordering': ['created_at'],
            },
        ),
    ]
