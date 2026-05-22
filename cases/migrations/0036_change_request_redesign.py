"""
Migration: Replace stage-based document approval system with simple change request flow.

Removes:
- DocumentApprovalLog
- DocumentApprovalStep
- DocumentApproverConfig
- DocumentApprovalStage
- CaseDocument
- CategoryApproverConfig
- CaseCategory.workflow_type

Adds:
- CaseCategory.enable_change_request
- ChangeRequestDocument
- ChangeRequestApproval
"""
import uuid
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cases', '0035_workflow_and_tracking'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [

        # ── Remove leaf models first (most dependent) ────────────────────────

        # DocumentApprovalLog references CaseDocument and DocumentApprovalStep
        migrations.DeleteModel(name='DocumentApprovalLog'),

        # DocumentApprovalStep references CaseDocument
        migrations.DeleteModel(name='DocumentApprovalStep'),

        # DocumentApproverConfig references DocumentApprovalStage
        migrations.DeleteModel(name='DocumentApproverConfig'),

        # DocumentApprovalStage references DocumentTemplate (kept)
        migrations.DeleteModel(name='DocumentApprovalStage'),

        # CaseDocument references CaseRecord and DocumentTemplate (both kept)
        migrations.DeleteModel(name='CaseDocument'),

        # CategoryApproverConfig references CaseCategory (kept)
        migrations.DeleteModel(name='CategoryApproverConfig'),

        # ── CaseCategory: swap workflow_type → enable_change_request ─────────

        migrations.RemoveField(
            model_name='casecategory',
            name='workflow_type',
        ),
        migrations.AddField(
            model_name='casecategory',
            name='enable_change_request',
            field=models.BooleanField(
                default=False,
                verbose_name='Enable Change Request',
                help_text='Allow portal users to attach a change request document when submitting tickets in this category.',
            ),
        ),

        # ── ChangeRequestDocument ────────────────────────────────────────────

        migrations.CreateModel(
            name='ChangeRequestDocument',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('request_change', models.TextField(verbose_name='Request Change')),
                ('chronology', models.TextField(verbose_name='Chronology')),
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
                    upload_to='change_requests/',
                    verbose_name='Generated PDF',
                )),
                ('revision_number', models.PositiveIntegerField(
                    default=1,
                    verbose_name='Revision Number',
                )),
                ('submitted_at', models.DateTimeField(blank=True, null=True, verbose_name='Submitted At')),
                ('case', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='change_requests',
                    to='cases.caserecord',
                    verbose_name='Ticket',
                )),
                ('created_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='cases_changerequestdocument_created',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Created By',
                )),
                ('updated_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='cases_changerequestdocument_updated',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Updated By',
                )),
            ],
            options={
                'verbose_name': 'Change Request Document',
                'verbose_name_plural': 'Change Request Documents',
                'ordering': ['-created_at'],
            },
        ),

        # ── ChangeRequestApproval ────────────────────────────────────────────

        migrations.CreateModel(
            name='ChangeRequestApproval',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('order', models.PositiveIntegerField(default=1, verbose_name='Approval Order')),
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
                ('notes', models.TextField(blank=True, verbose_name='Notes')),
                ('token', models.UUIDField(
                    db_index=True,
                    default=uuid.uuid4,
                    help_text='Magic-link token for approver access without login.',
                    unique=True,
                    verbose_name='Access Token',
                )),
                ('token_expires_at', models.DateTimeField(blank=True, null=True, verbose_name='Token Expires At')),
                ('acted_at', models.DateTimeField(blank=True, null=True, verbose_name='Acted At')),
                ('document', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='approvals',
                    to='cases.changerequestdocument',
                    verbose_name='Change Request Document',
                )),
                ('approver', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='change_request_approvals',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Approver',
                )),
                ('created_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='cases_changerequestapproval_created',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Created By',
                )),
                ('updated_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='cases_changerequestapproval_updated',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Updated By',
                )),
            ],
            options={
                'verbose_name': 'Change Request Approval',
                'verbose_name_plural': 'Change Request Approvals',
                'ordering': ['document', 'order'],
                'unique_together': {('document', 'approver')},
            },
        ),
    ]
