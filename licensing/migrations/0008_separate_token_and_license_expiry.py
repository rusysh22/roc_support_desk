from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('licensing', '0007_harden_installation_identity_and_trial'),
    ]

    operations = [
        migrations.AddField(
            model_name='licenserecord',
            name='token_expires_at',
            field=models.DateTimeField(
                blank=True,
                help_text='Activation-token refresh deadline. This never controls the commercial license expiry.',
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name='licenserecord',
            name='expires_at',
            field=models.DateTimeField(
                blank=True,
                help_text='Commercial license/subscription expiry from the Marketplace. Null = perpetual.',
                null=True,
            ),
        ),
    ]
