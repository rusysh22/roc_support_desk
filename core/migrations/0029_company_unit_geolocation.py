from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0028_fix_auditlog_index_names'),
    ]

    operations = [
        migrations.AddField(
            model_name='companyunit',
            name='address',
            field=models.TextField(blank=True, default='', help_text="Full street address of this unit's office.", verbose_name='Address'),
        ),
        migrations.AddField(
            model_name='companyunit',
            name='city',
            field=models.CharField(blank=True, default='', max_length=100, verbose_name='City'),
        ),
        migrations.AddField(
            model_name='companyunit',
            name='province',
            field=models.CharField(blank=True, default='', max_length=100, verbose_name='Province'),
        ),
        migrations.AddField(
            model_name='companyunit',
            name='latitude',
            field=models.DecimalField(blank=True, decimal_places=7, help_text='GPS latitude, e.g. -6.2088000', max_digits=10, null=True, verbose_name='Latitude'),
        ),
        migrations.AddField(
            model_name='companyunit',
            name='longitude',
            field=models.DecimalField(blank=True, decimal_places=7, help_text='GPS longitude, e.g. 106.8456000', max_digits=10, null=True, verbose_name='Longitude'),
        ),
    ]
