from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Adds hero_image to ManualLandingConfig.
    Uses SeparateDatabaseAndState so the SQL runs as ADD COLUMN IF NOT EXISTS —
    safe to apply even if a previous broken deploy already created the column.
    """

    dependencies = [
        ("manual", "0002_manuallanding_config"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql="""
                        ALTER TABLE manual_manuallandingconfig
                        ADD COLUMN IF NOT EXISTS hero_image varchar(100) NULL;
                    """,
                    reverse_sql="""
                        ALTER TABLE manual_manuallandingconfig
                        DROP COLUMN IF EXISTS hero_image;
                    """,
                ),
            ],
            state_operations=[
                migrations.AddField(
                    model_name="manuallandingconfig",
                    name="hero_image",
                    field=models.ImageField(
                        blank=True,
                        null=True,
                        upload_to="manual/hero/",
                        help_text="Optional background image for the landing page hero section.",
                    ),
                ),
            ],
        ),
    ]
