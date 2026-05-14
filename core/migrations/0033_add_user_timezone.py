from django.db import migrations, models


def add_timezone_safe(apps, schema_editor):
    """Add timezone column only if it does not already exist."""
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'core_user' AND column_name = 'timezone'
        """)
        if not cursor.fetchone():
            cursor.execute(
                "ALTER TABLE core_user "
                "ADD COLUMN timezone VARCHAR(64) NOT NULL DEFAULT 'Asia/Jakarta'"
            )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0032_alter_companyunit_options_companyunit_parent_and_more"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(add_timezone_safe, noop),
            ],
            state_operations=[
                migrations.AddField(
                    model_name="user",
                    name="timezone",
                    field=models.CharField(
                        default="Asia/Jakarta",
                        help_text="User's local timezone for displaying dates and times.",
                        max_length=64,
                        verbose_name="Timezone",
                    ),
                ),
            ],
        ),
    ]
