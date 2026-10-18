import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0043_add_user_module_access'),
    ]

    operations = [
        migrations.AddField(
            model_name='employee',
            name='user',
            field=models.OneToOneField(
                blank=True,
                help_text="Optional staff/portal user account. Matching uses this user's NIK.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='employee_profile',
                to='core.user',
                verbose_name='Linked User',
            ),
        ),
    ]
