import uuid

from django.db import migrations, models
from django.utils import timezone


def preserve_existing_trial_usage(apps, schema_editor):
    LicenseRecord = apps.get_model('licensing', 'LicenseRecord')
    TrialRecord = apps.get_model('licensing', 'TrialRecord')
    if TrialRecord.objects.exists():
        LicenseRecord.objects.update(trial_consumed_at=timezone.now())
    LicenseRecord.objects.filter(status='trial', trial_consumed_at__isnull=True).update(
        trial_consumed_at=timezone.now(),
    )


class Migration(migrations.Migration):

    dependencies = [
        ('licensing', '0006_alter_licenserecord_activation_token_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='licenserecord',
            name='installation_id',
            field=models.UUIDField(
                default=uuid.uuid4,
                editable=False,
                help_text='Stable random identifier created once for this installation.',
                unique=True,
            ),
        ),
        migrations.AddField(
            model_name='licenserecord',
            name='trial_consumed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='licenserecord',
            name='trial_started_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(preserve_existing_trial_usage, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='licenserecord',
            name='install_fingerprint',
            field=models.CharField(
                blank=True,
                help_text='SHA-256(installation_id + product_id) — binds license to this installation.',
                max_length=64,
            ),
        ),
        migrations.AlterField(
            model_name='licenserecord',
            name='status',
            field=models.CharField(
                choices=[
                    ('trial', 'Trial'), ('active', 'Active'), ('grace', 'Grace Period'),
                    ('partial_lock', 'Partial Lock'), ('expired', 'Expired'),
                    ('suspended', 'Suspended'), ('unlicensed', 'Unlicensed'),
                ],
                default='unlicensed', max_length=20,
            ),
        ),
    ]
