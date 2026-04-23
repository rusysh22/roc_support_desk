from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0024_encrypt_email_passwords"),
    ]

    operations = [
        # Add must_change_password to User
        migrations.AddField(
            model_name="user",
            name="must_change_password",
            field=models.BooleanField(
                default=False,
                verbose_name="Must Change Password",
                help_text="Force this user to change their password on next login.",
            ),
        ),
        # Create LoginAttempt model
        migrations.CreateModel(
            name="LoginAttempt",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("login_username", models.CharField(db_index=True, max_length=150)),
                ("ip_address", models.GenericIPAddressField(blank=True, null=True)),
                ("attempted_at", models.DateTimeField(auto_now_add=True)),
                ("success", models.BooleanField(default=False)),
            ],
            options={
                "verbose_name": "Login Attempt",
                "verbose_name_plural": "Login Attempts",
                "ordering": ["-attempted_at"],
            },
        ),
    ]
