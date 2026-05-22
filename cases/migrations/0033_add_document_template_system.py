"""
Migration: Add document template system.

New models:
- DocumentTemplate
- DocumentApproverConfig
- CaseDocument
- DocumentApprovalStep
"""
import uuid
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cases', '0032_add_client_last_read_at_to_caserecord'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='DocumentTemplate',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('title', models.CharField(max_length=200, verbose_name='Template Title')),
                ('description', models.TextField(blank=True, verbose_name='Description')),
                ('body_html', models.TextField(
                    help_text='HTML content with {{placeholder}} variables, e.g. {{nama_pemohon}}, {{tanggal}}.',
                    verbose_name='Document Body (HTML)',
                )),
                ('is_required', models.BooleanField(
                    default=False,
                    help_text='If checked, user must complete this document to submit the ticket.',
                    verbose_name='Required',
                )),
                ('approval_flow', models.CharField(
                    choices=[
                        ('none', 'No Approval Required'),
                        ('click_only', 'Click Validation Only'),
                        ('signature_only', 'Signature Only'),
                        ('both', 'Click Validation + Signature'),
                    ],
                    default='click_only',
                    max_length=20,
                    verbose_name='Approval Flow',
                )),
                ('categories', models.ManyToManyField(
                    blank=True,
                    help_text='Document fill-form appears on ticket submission for these categories.',
                    related_name='document_templates',
                    to='cases.casecategory',
                    verbose_name='Applicable Categories',
                )),
                ('created_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='cases_documenttemplate_created',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Created By',
                )),
                ('updated_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='cases_documenttemplate_updated',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Updated By',
                )),
            ],
            options={
                'verbose_name': 'Document Template',
                'verbose_name_plural': 'Document Templates',
                'ordering': ['title'],
            },
        ),
        migrations.CreateModel(
            name='DocumentApproverConfig',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('order', models.PositiveIntegerField(
                    default=1,
                    help_text='Lower numbers are notified first.',
                    verbose_name='Approval Order',
                )),
                ('template', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='approver_configs',
                    to='cases.documenttemplate',
                    verbose_name='Document Template',
                )),
                ('approver', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='document_approver_configs',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Approver',
                )),
                ('created_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='cases_documentapproverconfig_created',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Created By',
                )),
                ('updated_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='cases_documentapproverconfig_updated',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Updated By',
                )),
            ],
            options={
                'verbose_name': 'Document Approver Config',
                'verbose_name_plural': 'Document Approver Configs',
                'ordering': ['template', 'order'],
                'unique_together': {('template', 'approver')},
            },
        ),
        migrations.CreateModel(
            name='CaseDocument',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('filled_data', models.JSONField(
                    default=dict,
                    help_text="Placeholder values provided by the user, e.g. {'nama_pemohon': 'John Doe'}.",
                    verbose_name='Filled Data',
                )),
                ('status', models.CharField(
                    choices=[
                        ('draft', 'Draft'),
                        ('pending_approval', 'Pending Approval'),
                        ('approved', 'Approved'),
                        ('rejected', 'Rejected'),
                    ],
                    db_index=True,
                    default='draft',
                    max_length=20,
                    verbose_name='Status',
                )),
                ('generated_pdf', models.FileField(
                    blank=True,
                    null=True,
                    upload_to='documents/',
                    verbose_name='Generated PDF',
                )),
                ('token', models.UUIDField(
                    db_index=True,
                    default=uuid.uuid4,
                    help_text='Magic-link token for approver access without login.',
                    unique=True,
                    verbose_name='Access Token',
                )),
                ('submitted_at', models.DateTimeField(blank=True, null=True, verbose_name='Submitted At')),
                ('case', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='documents',
                    to='cases.caserecord',
                    verbose_name='Ticket',
                )),
                ('template', models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='generated_documents',
                    to='cases.documenttemplate',
                    verbose_name='Document Template',
                )),
                ('created_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='cases_casedocument_created',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Created By',
                )),
                ('updated_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='cases_casedocument_updated',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Updated By',
                )),
            ],
            options={
                'verbose_name': 'Case Document',
                'verbose_name_plural': 'Case Documents',
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='DocumentApprovalStep',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('order', models.PositiveIntegerField(default=1, verbose_name='Step Order')),
                ('status', models.CharField(
                    choices=[
                        ('pending', 'Pending'),
                        ('approved', 'Approved'),
                        ('rejected', 'Rejected'),
                    ],
                    db_index=True,
                    default='pending',
                    max_length=20,
                    verbose_name='Status',
                )),
                ('approval_type', models.CharField(
                    choices=[
                        ('click', 'Click Validation'),
                        ('signature', 'Digital Signature'),
                    ],
                    default='click',
                    max_length=20,
                    verbose_name='Approval Type',
                )),
                ('signature_image', models.FileField(
                    blank=True,
                    null=True,
                    upload_to='documents/signatures/',
                    verbose_name='Signature Image',
                )),
                ('notes', models.TextField(blank=True, verbose_name='Notes')),
                ('acted_at', models.DateTimeField(blank=True, null=True, verbose_name='Action Taken At')),
                ('document', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='approval_steps',
                    to='cases.casedocument',
                    verbose_name='Document',
                )),
                ('approver', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='document_approval_steps',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Approver',
                )),
                ('created_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='cases_documentapprovalstep_created',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Created By',
                )),
                ('updated_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='cases_documentapprovalstep_updated',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Updated By',
                )),
            ],
            options={
                'verbose_name': 'Document Approval Step',
                'verbose_name_plural': 'Document Approval Steps',
                'ordering': ['document', 'order'],
            },
        ),
    ]
