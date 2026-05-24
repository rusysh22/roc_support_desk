from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0039_alter_aiconfig_ai_max_context_docs_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='siteconfig',
            name='job_role_mode',
            field=models.CharField(
                choices=[('freetext', 'Free Text'), ('master', 'Master Data')],
                default='freetext',
                help_text=(
                    'Free Text: users type their job role freely. '
                    'Master Data: users pick from the Job Roles master list.'
                ),
                max_length=10,
                verbose_name='Job Role Input Mode',
            ),
        ),
        migrations.CreateModel(
            name='JobRole',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=150, unique=True, verbose_name='Job Role Name')),
                ('is_active', models.BooleanField(default=True, verbose_name='Active')),
                ('order', models.PositiveIntegerField(default=0, verbose_name='Display Order')),
            ],
            options={
                'verbose_name': 'Job Role',
                'verbose_name_plural': 'Job Roles',
                'ordering': ['order', 'name'],
            },
        ),
    ]
