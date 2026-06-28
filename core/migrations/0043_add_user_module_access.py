import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0042_siteconfig_marketplace_url_siteconfig_site_url_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='employee',
            name='reports_to',
            field=models.ForeignKey(
                blank=True,
                help_text='The direct manager or supervisor of this employee.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='subordinates',
                to='core.employee',
                verbose_name='Reports To (Manager)',
            ),
        ),
        migrations.AddField(
            model_name='user',
            name='module_access',
            field=models.JSONField(
                blank=True,
                default=None,
                help_text='List of module slugs this user can access. Leave null to inherit role defaults.',
                null=True,
                verbose_name='Module Access',
            ),
        ),
    ]
