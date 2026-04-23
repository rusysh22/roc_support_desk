from django.db import migrations
from encrypted_model_fields.fields import EncryptedCharField


class Migration(migrations.Migration):
    """
    Encrypts User.phone_number at rest using django-encrypted-model-fields.
    Existing plaintext values are re-encrypted automatically by the migration
    framework when it resolves the new field definition.

    Fields NOT encrypted in this migration (blocked by DB-level search usage):
      - User.nik            — used in nik__icontains search (views_user.py)
      - Employee.email      — used in email__iexact lookup (gateways/tasks.py)
      - Employee.phone_number — used in icontains searches (cases/views.py)
    These require application-level search refactoring before they can be encrypted.
    """

    dependencies = [
        ("core", "0026_add_auditlog"),
    ]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="phone_number",
            field=EncryptedCharField(
                blank=True,
                default="",
                max_length=30,
                verbose_name="Phone Number",
                help_text="User's phone/mobile number (encrypted at rest).",
            ),
        ),
    ]
