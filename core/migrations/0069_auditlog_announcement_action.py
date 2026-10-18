from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0068_teams_personal_notifications"),
    ]

    operations = [
        migrations.AlterField(
            model_name="auditlog",
            name="action",
            field=models.CharField(
                choices=[
                    ("LOGIN_SUCCESS", "Login — Success"),
                    ("LOGIN_FAIL", "Login — Failed"),
                    ("LOGOUT", "Logout"),
                    ("PASSWORD_CHANGE", "Password Changed"),
                    ("BULK_IMPORT", "Bulk User Import"),
                    ("EXPORT", "Data Export"),
                    ("ROLE_CHANGE", "Role Changed"),
                    ("SMTP_UPDATE", "SMTP/IMAP Config Updated"),
                    ("TICKET_CLOSE", "Ticket Closed"),
                    ("TICKET_REOPEN", "Ticket Reopened"),
                    ("LICENSE_OP", "License Operation"),
                    ("ACCOUNT_REQUEST", "Account Request Submitted"),
                    ("ANNOUNCEMENT_PUBLISH", "Announcement Published"),
                ],
                db_index=True,
                max_length=30,
            ),
        ),
    ]
