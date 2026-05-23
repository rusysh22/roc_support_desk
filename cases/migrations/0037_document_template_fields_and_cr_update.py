"""
Migration: Add DocumentTemplateField model and update ChangeRequestDocument.

Uses SeparateDatabaseAndState + IF NOT EXISTS / IF EXISTS SQL throughout
to be idempotent against partial database states from prior failed migration
attempts (e.g. columns that already exist, tables partially created).
"""
import uuid
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cases', '0036_change_request_redesign'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [

        # ── Remove deprecated fields from DocumentTemplate ────────────────

        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(model_name='documenttemplate', name='approval_flow'),
                migrations.RemoveField(model_name='documenttemplate', name='approver_deadline_days'),
                migrations.RemoveField(model_name='documenttemplate', name='is_required'),
                migrations.RemoveField(model_name='documenttemplate', name='token_validity_days'),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql="""
                        ALTER TABLE cases_documenttemplate DROP COLUMN IF EXISTS approval_flow;
                        ALTER TABLE cases_documenttemplate DROP COLUMN IF EXISTS approver_deadline_days;
                        ALTER TABLE cases_documenttemplate DROP COLUMN IF EXISTS is_required;
                        ALTER TABLE cases_documenttemplate DROP COLUMN IF EXISTS token_validity_days;
                    """,
                    reverse_sql="",
                ),
            ],
        ),

        # ── Add document_template FK to ChangeRequestDocument ─────────────
        # Column may already exist — use IF NOT EXISTS to be safe.

        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name='changerequestdocument',
                    name='document_template',
                    field=models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='documents',
                        to='cases.documenttemplate',
                        verbose_name='Document Template',
                    ),
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql="""
                        ALTER TABLE cases_changerequestdocument
                        ADD COLUMN IF NOT EXISTS document_template_id UUID
                        REFERENCES cases_documenttemplate(id) ON DELETE SET NULL;
                    """,
                    reverse_sql="ALTER TABLE cases_changerequestdocument DROP COLUMN IF EXISTS document_template_id;",
                ),
            ],
        ),

        # ── Add field_values JSONField to ChangeRequestDocument ────────────

        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddField(
                    model_name='changerequestdocument',
                    name='field_values',
                    field=models.JSONField(
                        blank=True,
                        default=list,
                        verbose_name='Field Values',
                    ),
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql="""
                        ALTER TABLE cases_changerequestdocument
                        ADD COLUMN IF NOT EXISTS field_values JSONB NOT NULL DEFAULT '[]'::JSONB;
                    """,
                    reverse_sql="ALTER TABLE cases_changerequestdocument DROP COLUMN IF EXISTS field_values;",
                ),
            ],
        ),

        # ── Make request_change and chronology blank=True ─────────────────
        # No database change needed — blank only affects form validation.

        migrations.AlterField(
            model_name='changerequestdocument',
            name='request_change',
            field=models.TextField(blank=True, verbose_name='Request Change'),
        ),
        migrations.AlterField(
            model_name='changerequestdocument',
            name='chronology',
            field=models.TextField(blank=True, verbose_name='Chronology'),
        ),

        # ── Create DocumentTemplateField model ────────────────────────────
        # Table may already exist (without default_content). Use IF NOT EXISTS
        # for the CREATE TABLE then add the missing column separately.

        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name='DocumentTemplateField',
                    fields=[
                        ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                        ('created_at', models.DateTimeField(auto_now_add=True)),
                        ('updated_at', models.DateTimeField(auto_now=True)),
                        ('order', models.PositiveSmallIntegerField(
                            default=1,
                            help_text='Display order (1 = first). Maximum 10 fields per template.',
                            verbose_name='Order',
                        )),
                        ('label', models.CharField(max_length=200, verbose_name='Field Label')),
                        ('placeholder', models.CharField(
                            blank=True,
                            help_text='Optional hint text shown inside the empty editor.',
                            max_length=300,
                            verbose_name='Placeholder Hint',
                        )),
                        ('default_content', models.TextField(
                            blank=True,
                            help_text='Optional HTML template pre-filled into the editor when the user opens the form.',
                            verbose_name='Default Content',
                        )),
                        ('is_required', models.BooleanField(default=True, verbose_name='Required')),
                        ('template', models.ForeignKey(
                            on_delete=django.db.models.deletion.CASCADE,
                            related_name='fields',
                            to='cases.documenttemplate',
                            verbose_name='Template',
                        )),
                        ('created_by', models.ForeignKey(
                            blank=True,
                            null=True,
                            on_delete=django.db.models.deletion.SET_NULL,
                            related_name='cases_documenttemplatefield_created',
                            to=settings.AUTH_USER_MODEL,
                            verbose_name='Created By',
                        )),
                        ('updated_by', models.ForeignKey(
                            blank=True,
                            null=True,
                            on_delete=django.db.models.deletion.SET_NULL,
                            related_name='cases_documenttemplatefield_updated',
                            to=settings.AUTH_USER_MODEL,
                            verbose_name='Updated By',
                        )),
                    ],
                    options={
                        'verbose_name': 'Template Field',
                        'verbose_name_plural': 'Template Fields',
                        'ordering': ['template', 'order'],
                        'unique_together': {('template', 'order')},
                    },
                ),
            ],
            database_operations=[
                # Create the table if it doesn't exist yet
                migrations.RunSQL(
                    sql="""
                        CREATE TABLE IF NOT EXISTS cases_documenttemplatefield (
                            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            "order" SMALLINT NOT NULL DEFAULT 1,
                            label VARCHAR(200) NOT NULL DEFAULT '',
                            placeholder VARCHAR(300) NOT NULL DEFAULT '',
                            is_required BOOLEAN NOT NULL DEFAULT TRUE,
                            template_id UUID NOT NULL
                                REFERENCES cases_documenttemplate(id) ON DELETE CASCADE,
                            created_by_id INTEGER
                                REFERENCES auth_user(id) ON DELETE SET NULL,
                            updated_by_id INTEGER
                                REFERENCES auth_user(id) ON DELETE SET NULL
                        );
                    """,
                    reverse_sql="DROP TABLE IF EXISTS cases_documenttemplatefield;",
                ),
                # Add default_content separately — safe whether table is new or pre-existing
                migrations.RunSQL(
                    sql="""
                        ALTER TABLE cases_documenttemplatefield
                        ADD COLUMN IF NOT EXISTS default_content TEXT NOT NULL DEFAULT '';
                    """,
                    reverse_sql="ALTER TABLE cases_documenttemplatefield DROP COLUMN IF EXISTS default_content;",
                ),
                # Ensure unique constraint exists (idempotent via DO block)
                migrations.RunSQL(
                    sql="""
                        DO $$
                        BEGIN
                            IF NOT EXISTS (
                                SELECT 1 FROM pg_constraint
                                WHERE conname = 'cases_documenttemplatefield_template_id_order_uniq'
                                  AND conrelid = 'cases_documenttemplatefield'::regclass
                            ) THEN
                                ALTER TABLE cases_documenttemplatefield
                                ADD CONSTRAINT cases_documenttemplatefield_template_id_order_uniq
                                UNIQUE (template_id, "order");
                            END IF;
                        END $$;
                    """,
                    reverse_sql="",
                ),
            ],
        ),
    ]
