from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0037_siteconfig_wa_gateway_config"),
    ]

    operations = [
        # Rate limiter
        migrations.AddField(
            model_name="siteconfig",
            name="wa_rate_recipient_cooldown",
            field=models.PositiveSmallIntegerField(
                default=10,
                verbose_name="WA Recipient Cooldown (s)",
                help_text="Minimum seconds between two messages to the same phone number. Recommended: 10.",
            ),
        ),
        migrations.AddField(
            model_name="siteconfig",
            name="wa_rate_burst_max",
            field=models.PositiveSmallIntegerField(
                default=8,
                verbose_name="WA Burst Limit (per 60s)",
                help_text="Maximum outbound messages in any rolling 60-second window. Recommended: 8.",
            ),
        ),
        migrations.AddField(
            model_name="siteconfig",
            name="wa_rate_minute_max",
            field=models.PositiveSmallIntegerField(
                default=15,
                verbose_name="WA Per-Minute Limit",
                help_text="Maximum outbound messages per minute. Recommended: 15.",
            ),
        ),
        migrations.AddField(
            model_name="siteconfig",
            name="wa_rate_hour_max",
            field=models.PositiveIntegerField(
                default=200,
                verbose_name="WA Per-Hour Limit",
                help_text="Maximum outbound messages per hour. Recommended: 200.",
            ),
        ),
        # Circuit breaker
        migrations.AddField(
            model_name="siteconfig",
            name="wa_cb_max_disconnects",
            field=models.PositiveSmallIntegerField(
                default=2,
                verbose_name="WA CB Max Disconnects",
                help_text="Disconnects within 24h before circuit breaker trips. Recommended: 2.",
            ),
        ),
        migrations.AddField(
            model_name="siteconfig",
            name="wa_cb_block_hours",
            field=models.PositiveSmallIntegerField(
                default=4,
                verbose_name="WA CB Block Duration (hours)",
                help_text="Hours all outbound sends stay blocked after circuit trips. Recommended: 4.",
            ),
        ),
        # Human pacing
        migrations.AddField(
            model_name="siteconfig",
            name="wa_read_pause_min_ms",
            field=models.PositiveSmallIntegerField(
                default=1000,
                verbose_name="WA Read Pause Min (ms)",
                help_text="Minimum read pause in milliseconds before typing. Recommended: 1000.",
            ),
        ),
        migrations.AddField(
            model_name="siteconfig",
            name="wa_read_pause_max_ms",
            field=models.PositiveSmallIntegerField(
                default=3500,
                verbose_name="WA Read Pause Max (ms)",
                help_text="Maximum read pause in milliseconds before typing. Recommended: 3500.",
            ),
        ),
        migrations.AddField(
            model_name="siteconfig",
            name="wa_typing_speed_cps",
            field=models.PositiveSmallIntegerField(
                default=25,
                verbose_name="WA Typing Speed (chars/s)",
                help_text="Simulated typing speed in characters per second. Recommended: 25.",
            ),
        ),
        # Opt-in guard
        migrations.AddField(
            model_name="siteconfig",
            name="wa_opt_in_ttl_days",
            field=models.PositiveSmallIntegerField(
                default=7,
                verbose_name="WA Opt-in TTL (days)",
                help_text="Days a contact stays opted-in after their last inbound message. Recommended: 7.",
            ),
        ),
    ]
