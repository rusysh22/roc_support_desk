"""
Migration: Workflow types, stage-based approval, and full audit tracking.

Changes:
- CaseCategory: add workflow_type field
- DocumentApprovalStage: new model (stages with ALL_REQUIRED/ANY_ONE policy)
- DocumentApproverConfig: replace template FK with stage FK (stage-based)
- CaseDocument: make template nullable, add revision_number
- DocumentApprovalStep: add stage_order + stage_policy snapshot fields
- CategoryApproverConfig: new model (ticket-level approvers for APPROVAL_ONLY)
- DocumentApprovalLog: new model (full audit trail for every approval action)

Note: CaseRecord.Status additions (PendingApproval, RevisionRequired) and
DocumentApprovalStep.Status addition (skipped) are Python-only changes —
no DB column alteration needed (no DB-level CHECK constraint in Django).
"""
import uuid
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cases', '0034_document_expiry_and_deadline'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [

        # ── CaseCategory: workflow_type ──────────────────────────────────────
        migrations.AddField(
            model_name='casecategory',
            name='workflow_type',
            field=models.CharField(
                choices=[
                    ('standard', 'Standard (no document/approval)'),
                    ('document_only', 'Document Only (attach, no approval)'),
                    ('approval_only', 'Approval Only (no document)'),
                    ('document_approval', 'Document + Approval'),
                ],
                default='standard',
                max_length=20,
                verbose_name='Workflow Type',
                help_text='Controls whether tickets in this category require documents, approvals, both, or neither.',
            ),
        ),

        # ── DocumentApprovalStage: new model ─────────────────────────────────
        migrations.CreateModel(
            name='DocumentApprovalStage',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('order', models.PositiveIntegerField(
                    default=1,
                    help_text='Stages are processed in ascending order.',
                    verbose_name='Stage Order',
                )),
                ('label', models.CharField(
                    blank=True,
                    max_length=100,
                    verbose_name='Stage Label',
                    help_text="Optional name shown in the UI, e.g. 'Manager Sign-off'.",
                )),
                ('policy', models.CharField(
                    choices=[
                        ('all_required', 'All Must Approve'),
                        ('any_one', 'Any One May Approve'),
                    ],
                    default='all_required',
                    max_length=20,
                    verbose_name='Stage Policy',
                )),
                ('allow_user_selection', models.BooleanField(
                    default=False,
                    verbose_name='Allow User Selection',
                    help_text='If checked, the submitter may choose approvers for this stage when submitting.',
                )),
                ('template', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='approval_stages',
                    to='cases.documenttemplate',
                    verbose_name='Document Template',
                )),
                ('created_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='cases_documentapprovalstage_created',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Created By',
                )),
                ('updated_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='cases_documentapprovalstage_updated',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Updated By',
                )),
            ],
            options={
                'verbose_name': 'Document Approval Stage',
                'verbose_name_plural': 'Document Approval Stages',
                'ordering': ['template', 'order'],
                'unique_together': {('template', 'order')},
            },
        ),

        # ── DocumentApproverConfig: swap template FK → stage FK ──────────────
        # 1. Clear the old unique_together before removing the column
        migrations.AlterUniqueTogether(
            name='documentapproverconfig',
            unique_together=set(),
        ),
        # 2. Remove old template FK
        migrations.RemoveField(
            model_name='documentapproverconfig',
            name='template',
        ),
        # 3. Add new stage FK (nullable so existing empty rows don't break)
        migrations.AddField(
            model_name='documentapproverconfig',
            name='stage',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='approver_configs',
                to='cases.documentapprovalstage',
                verbose_name='Approval Stage',
            ),
        ),
        # 4. Update ordering meta
        migrations.AlterModelOptions(
            name='documentapproverconfig',
            options={
                'verbose_name': 'Document Approver Config',
                'verbose_name_plural': 'Document Approver Configs',
                'ordering': ['stage', 'order'],
            },
        ),
        # 5. Restore unique_together on the new column
        migrations.AlterUniqueTogether(
            name='documentapproverconfig',
            unique_together={('stage', 'approver')},
        ),

        # ── CaseDocument: nullable template + revision_number ────────────────
        migrations.AlterField(
            model_name='casedocument',
            name='template',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='generated_documents',
                to='cases.documenttemplate',
                verbose_name='Document Template',
                help_text='Null when workflow_type=APPROVAL_ONLY (no document body needed).',
            ),
        ),
        migrations.AddField(
            model_name='casedocument',
            name='revision_number',
            field=models.PositiveIntegerField(
                default=1,
                verbose_name='Revision Number',
                help_text='Increments each time the user submits a revision after rejection.',
            ),
        ),

        # ── DocumentApprovalStep: stage context snapshot fields ───────────────
        migrations.AddField(
            model_name='documentapprovalstep',
            name='stage_order',
            field=models.PositiveIntegerField(
                default=1,
                verbose_name='Stage Order',
                help_text='Which stage this step belongs to (copied from DocumentApprovalStage.order at creation).',
            ),
        ),
        migrations.AddField(
            model_name='documentapprovalstep',
            name='stage_policy',
            field=models.CharField(
                blank=True,
                max_length=20,
                verbose_name='Stage Policy Snapshot',
                help_text='Policy of the stage at the time this step was created (all_required or any_one).',
            ),
        ),

        # ── CategoryApproverConfig: ticket-level approvers ───────────────────
        migrations.CreateModel(
            name='CategoryApproverConfig',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('order', models.PositiveIntegerField(
                    default=1,
                    verbose_name='Approval Order',
                    help_text='Lower numbers are notified first.',
                )),
                ('category', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='approver_configs',
                    to='cases.casecategory',
                    verbose_name='Category',
                )),
                ('approver', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='category_approver_configs',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Approver',
                )),
                ('created_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='cases_categoryapproverconfig_created',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Created By',
                )),
                ('updated_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='cases_categoryapproverconfig_updated',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Updated By',
                )),
            ],
            options={
                'verbose_name': 'Category Approver Config',
                'verbose_name_plural': 'Category Approver Configs',
                'ordering': ['category', 'order'],
                'unique_together': {('category', 'approver')},
            },
        ),

        # ── DocumentApprovalLog: full audit trail ────────────────────────────
        migrations.CreateModel(
            name='DocumentApprovalLog',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('action', models.CharField(
                    choices=[
                        ('submitted', 'Document Submitted'),
                        ('stage_started', 'Approval Stage Started'),
                        ('approved', 'Approved'),
                        ('rejected', 'Rejected'),
                        ('skipped', 'Step Skipped (ANY_ONE satisfied)'),
                        ('revision_requested', 'Revision Requested'),
                        ('revision_submitted', 'Revision Submitted'),
                        ('token_expired', 'Approval Link Expired'),
                        ('fully_approved', 'All Approvals Completed'),
                        ('ticket_activated', 'Ticket Activated (OPEN)'),
                    ],
                    db_index=True,
                    max_length=30,
                    verbose_name='Action',
                )),
                ('actor_name', models.CharField(
                    blank=True,
                    max_length=255,
                    verbose_name='Actor Name (snapshot)',
                    help_text='Display name at time of action, preserved for anonymous/magic-link approvers.',
                )),
                ('stage_order', models.PositiveIntegerField(
                    blank=True, null=True,
                    verbose_name='Stage Order',
                )),
                ('notes', models.TextField(
                    blank=True,
                    verbose_name='Notes / Rejection Reason',
                    help_text='Rejection message or any actor-supplied notes for this action.',
                )),
                ('previous_status', models.CharField(blank=True, max_length=30, verbose_name='Previous Status')),
                ('new_status', models.CharField(blank=True, max_length=30, verbose_name='New Status')),
                ('ip_address', models.GenericIPAddressField(
                    blank=True, null=True,
                    verbose_name='IP Address',
                    help_text='IP of the actor at the time of action (for magic-link approvers).',
                )),
                ('document', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='approval_logs',
                    to='cases.casedocument',
                    verbose_name='Document',
                )),
                ('actor', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='document_approval_actions',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Actor',
                    help_text='Staff/approver who performed the action. Null for magic-link or system actions.',
                )),
                ('step', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='audit_logs',
                    to='cases.documentapprovalstep',
                    verbose_name='Approval Step',
                )),
                ('created_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='cases_documentapprovallog_created',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Created By',
                )),
                ('updated_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='cases_documentapprovallog_updated',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Updated By',
                )),
            ],
            options={
                'verbose_name': 'Document Approval Log',
                'verbose_name_plural': 'Document Approval Logs',
                'ordering': ['created_at'],
            },
        ),
    ]
