from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Creates ManualLandingConfig singleton table.
    Uses SeparateDatabaseAndState so the SQL runs as IF NOT EXISTS — safe to
    apply even if a previous broken deploy already created the table.
    """

    dependencies = [
        ("manual", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            # Raw SQL: idempotent — won't fail if the table already exists.
            database_operations=[
                migrations.RunSQL(
                    sql="""
                        CREATE TABLE IF NOT EXISTS manual_manuallandingconfig (
                            id         bigserial    PRIMARY KEY,
                            hero_title varchar(200) NOT NULL
                                       DEFAULT 'Documentation & User Guide',
                            hero_subtitle text      NOT NULL DEFAULT '',
                            updated_at timestamptz  NOT NULL DEFAULT NOW()
                        );
                    """,
                    reverse_sql="DROP TABLE IF EXISTS manual_manuallandingconfig;",
                ),
            ],
            # ORM state: tells Django the model exists from this migration onward.
            state_operations=[
                migrations.CreateModel(
                    name="ManualLandingConfig",
                    fields=[
                        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                        ("hero_title", models.CharField(default="Documentation & User Guide", max_length=200)),
                        ("hero_subtitle", models.TextField(blank=True, default="Select an application manual to get started.")),
                        ("updated_at", models.DateTimeField(auto_now=True)),
                    ],
                    options={
                        "verbose_name": "Landing Page Config",
                    },
                ),
            ],
        ),
    ]
