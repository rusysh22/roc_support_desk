import django.db.models.deletion
import encrypted_model_fields.fields
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0029_company_unit_geolocation'),
    ]

    operations = [
        migrations.CreateModel(
            name='TeamsConfig',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Created At')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Updated At')),
                ('incoming_webhook_url', encrypted_model_fields.fields.EncryptedCharField(
                    blank=True, null=True,
                    help_text='From Teams channel → Connectors → Incoming Webhook',
                    verbose_name='Incoming Webhook URL',
                )),
                ('is_notification_enabled', models.BooleanField(default=False, verbose_name='Enable Teams Notifications')),
                ('is_bot_enabled', models.BooleanField(default=False, verbose_name='Enable Teams Bot (2-Way)')),
                ('bot_app_id', models.CharField(blank=True, max_length=255, null=True, help_text='Azure Bot Service App ID (GUID)', verbose_name='Bot App ID')),
                ('bot_app_password', encrypted_model_fields.fields.EncryptedCharField(
                    blank=True, null=True,
                    verbose_name='Bot App Password / Client Secret',
                )),
                ('tenant_id', models.CharField(blank=True, max_length=255, null=True, verbose_name='Azure Tenant ID')),
                ('bot_webhook_secret', encrypted_model_fields.fields.EncryptedCharField(
                    blank=True, null=True,
                    help_text='Used to verify inbound webhook requests from Teams',
                    verbose_name='Bot Webhook Secret',
                )),
                ('service_url', models.CharField(
                    blank=True, max_length=500, null=True,
                    help_text='Auto-filled from first incoming Teams Bot message — do not edit manually',
                    verbose_name='Service URL',
                )),
                ('created_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='%(app_label)s_%(class)s_created',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Created By',
                )),
                ('updated_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='%(app_label)s_%(class)s_updated',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Updated By',
                )),
            ],
            options={
                'verbose_name': 'Teams Configuration',
                'verbose_name_plural': 'Teams Configuration',
            },
        ),
        migrations.CreateModel(
            name='NotificationConfig',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Created At')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Updated At')),
                ('notify_new_ticket_email', models.BooleanField(default=False, verbose_name='Email Notification')),
                ('notify_new_ticket_whatsapp', models.BooleanField(default=False, verbose_name='WhatsApp Notification')),
                ('notify_new_ticket_teams', models.BooleanField(default=False, verbose_name='Teams Notification')),
                ('email_recipients', models.TextField(
                    blank=True,
                    help_text='Comma-separated email addresses, e.g. agen1@corp.com, agen2@corp.com',
                    verbose_name='Email Recipients',
                )),
                ('whatsapp_recipients', models.TextField(
                    blank=True,
                    help_text='Comma-separated numbers in E.164 format, e.g. 628123456789, 628987654321',
                    verbose_name='WhatsApp Recipients',
                )),
                ('created_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='%(app_label)s_%(class)s_created',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Created By',
                )),
                ('updated_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='%(app_label)s_%(class)s_updated',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Updated By',
                )),
            ],
            options={
                'verbose_name': 'Notification Configuration',
                'verbose_name_plural': 'Notification Configuration',
            },
        ),
    ]
