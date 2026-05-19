from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0035_add_ai_config"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql="""
                        ALTER TABLE core_siteconfig
                        ADD COLUMN IF NOT EXISTS wa_instance_activated_at
                        TIMESTAMP WITH TIME ZONE NULL;
                    """,
                    reverse_sql="""
                        ALTER TABLE core_siteconfig
                        DROP COLUMN IF EXISTS wa_instance_activated_at;
                    """,
                ),
            ],
            state_operations=[
                migrations.AddField(
                    model_name="siteconfig",
                    name="wa_instance_activated_at",
                    field=models.DateTimeField(
                        blank=True,
                        null=True,
                        verbose_name="WA Instance Activation Date",
                        help_text=(
                            "Set this to the date the WhatsApp number was first connected. "
                            "Used to enforce a warm-up period (lower send caps in the first 14 days) "
                            "to reduce the chance of being flagged by WhatsApp."
                        ),
                    ),
                ),
            ],
        ),
    ]
