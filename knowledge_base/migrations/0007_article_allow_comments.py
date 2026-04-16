from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('knowledge_base', '0006_articlecomment'),
    ]

    operations = [
        migrations.AddField(
            model_name='article',
            name='allow_comments',
            field=models.BooleanField(
                default=True,
                help_text='Allow logged-in users to post comments on this article.',
                verbose_name='Allow Comments',
            ),
        ),
    ]
