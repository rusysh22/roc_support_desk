from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('licensing', '0008_separate_token_and_license_expiry')]

    operations = [
        migrations.AlterField(
            model_name='licenseauditlog',
            name='event',
            field=models.CharField(
                max_length=32,
                choices=[
                    ('webhook_received', 'Webhook Received'),
                    ('activated', 'License Activated'),
                    ('renewed', 'License Renewed'),
                    ('upgraded', 'License Upgraded'),
                    ('deactivated', 'License Deactivated'),
                    ('expired', 'License Expired'),
                    ('suspended', 'License Suspended'),
                    ('trial_started', 'Trial Started'),
                    ('trial_ended', 'Trial Ended'),
                    ('verification_ok', 'Online Verification Succeeded'),
                    ('verification_failed', 'Online Verification Failed'),
                    ('verification_requested', 'Online Verification Requested'),
                    ('fraud_attempt', 'Fraud Attempt Detected'),
                    ('disconnected_wa', 'WhatsApp Disconnected (License)'),
                    ('disconnected_email', 'Email Disconnected (License)'),
                ],
            ),
        ),
    ]
