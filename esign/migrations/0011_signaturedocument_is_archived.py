from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("esign", "0010_signaturedocument_progress_token"),
    ]

    operations = [
        migrations.AddField(
            model_name="signaturedocument",
            name="is_archived",
            field=models.BooleanField(db_index=True, default=False, verbose_name="Archived"),
        ),
    ]
