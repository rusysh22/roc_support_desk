from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0036_siteconfig_wa_instance_activated_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="siteconfig",
            name="wa_main_instance",
            field=models.CharField(
                blank=True,
                default="",
                max_length=100,
                verbose_name="WA Main Instance Name",
                help_text=(
                    "Evolution API instance name for customer-facing messages. "
                    "Overrides EVOLUTION_INSTANCE_NAME in .env when set."
                ),
            ),
        ),
        migrations.AddField(
            model_name="siteconfig",
            name="wa_notif_instance",
            field=models.CharField(
                blank=True,
                default="",
                max_length=100,
                verbose_name="WA Notif Instance Name",
                help_text=(
                    "Evolution API instance name for internal staff notifications. "
                    "Overrides EVOLUTION_NOTIF_INSTANCE_NAME in .env when set. "
                    "Leave blank to use the same instance as customer messages."
                ),
            ),
        ),
        migrations.AddField(
            model_name="siteconfig",
            name="wa_business_hour_start",
            field=models.PositiveSmallIntegerField(
                default=7,
                verbose_name="WA Business Hour Start",
                help_text="Hour (0-23) when internal WA broadcasts become active. Default: 7 (07:00 WIB).",
            ),
        ),
        migrations.AddField(
            model_name="siteconfig",
            name="wa_business_hour_end",
            field=models.PositiveSmallIntegerField(
                default=20,
                verbose_name="WA Business Hour End",
                help_text="Hour (0-23) after which internal WA broadcasts are paused. Default: 20 (20:00 WIB).",
            ),
        ),
        migrations.AddField(
            model_name="siteconfig",
            name="wa_business_days",
            field=models.CharField(
                default="0,1,2,3,4",
                max_length=20,
                verbose_name="WA Business Days",
                help_text=(
                    "Comma-separated weekday numbers (0=Mon ... 6=Sun) when broadcasts are allowed. "
                    "Default: 0,1,2,3,4 (Monday-Friday)."
                ),
            ),
        ),
    ]
