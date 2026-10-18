from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0044_employee_user"),
    ]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="role_access",
            field=models.CharField(
                choices=[
                    ("SuperAdmin", "Super Admin"),
                    ("Manager", "Manager"),
                    ("SupportDesk", "Support Desk"),
                    ("Engineer", "Engineer"),
                    ("Auditor", "Auditor"),
                    ("PortalUser", "Portal User"),
                ],
                default="SupportDesk",
                max_length=20,
                verbose_name="Role Access",
            ),
        ),
    ]
