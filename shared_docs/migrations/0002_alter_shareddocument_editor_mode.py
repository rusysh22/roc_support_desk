from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shared_docs', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='shareddocument',
            name='editor_mode',
            field=models.CharField(
                choices=[
                    ('RICH_TEXT', 'Rich Text (Quill)'),
                    ('RAW_HTML', 'Raw HTML/CSS Code'),
                    ('MARKDOWN', 'Markdown'),
                ],
                default='RICH_TEXT',
                help_text='Choose Raw HTML to embed a complete custom HTML/CSS document.',
                max_length=20,
                verbose_name='Editor Mode',
            ),
        ),
    ]
