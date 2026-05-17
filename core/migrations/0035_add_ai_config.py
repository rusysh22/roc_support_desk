"""
Migration: Add AIConfig model for the Gemini AI Assistant widget.

All AI parameters are configurable from the Admin panel.
API key is stored encrypted via EncryptedCharField.
"""
import uuid
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import encrypted_model_fields.fields


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0034_add_user_notification_preference"),
    ]

    operations = [
        migrations.CreateModel(
            name="AIConfig",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="Created At"),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True, verbose_name="Updated At"),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="core_aiconfig_created",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Created By",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="core_aiconfig_updated",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Updated By",
                    ),
                ),
                (
                    "ai_enabled",
                    models.BooleanField(
                        default=False,
                        help_text="Show the 'Tanya AI' tab in the floating chat widget.",
                        verbose_name="Enable AI Assistant",
                    ),
                ),
                (
                    "ai_provider",
                    models.CharField(
                        choices=[("gemini", "Google Gemini")],
                        default="gemini",
                        max_length=30,
                        verbose_name="AI Provider",
                    ),
                ),
                (
                    "ai_api_key",
                    encrypted_model_fields.fields.EncryptedCharField(
                        blank=True,
                        default="",
                        help_text="Your Google AI Studio API key (stored encrypted).",
                        max_length=500,
                        verbose_name="API Key",
                    ),
                ),
                (
                    "ai_model_name",
                    models.CharField(
                        choices=[
                            ("gemini-2.0-flash", "Gemini 2.0 Flash (Recommended — Fast & Cheap)"),
                            ("gemini-2.0-flash-lite", "Gemini 2.0 Flash Lite (Cheapest)"),
                            ("gemini-1.5-pro", "Gemini 1.5 Pro (Most Powerful)"),
                            ("gemini-1.5-flash", "Gemini 1.5 Flash"),
                        ],
                        default="gemini-2.0-flash",
                        max_length=100,
                        verbose_name="Gemini Model",
                    ),
                ),
                (
                    "ai_temperature",
                    models.FloatField(
                        default=0.3,
                        help_text="Controls creativity (0.0 = deterministic, 1.0 = creative). Recommended: 0.3 for factual Q&A.",
                        verbose_name="Temperature",
                    ),
                ),
                (
                    "ai_max_output_tokens",
                    models.PositiveIntegerField(
                        default=1024,
                        help_text="Maximum length of the AI's response in tokens (~750 words).",
                        verbose_name="Max Output Tokens",
                    ),
                ),
                (
                    "ai_sources",
                    models.CharField(
                        choices=[
                            ("both", "Knowledge Base + User Manual"),
                            ("kb_only", "Knowledge Base Only"),
                            ("manual_only", "User Manual Only"),
                        ],
                        default="both",
                        help_text="Which knowledge sources the AI will use to answer questions.",
                        max_length=20,
                        verbose_name="Knowledge Sources",
                    ),
                ),
                (
                    "ai_max_context_docs",
                    models.PositiveIntegerField(
                        default=5,
                        help_text="Maximum number of relevant articles/pages to include in each AI query.",
                        verbose_name="Max Context Documents",
                    ),
                ),
                (
                    "ai_system_prompt",
                    models.TextField(
                        default=(
                            "You are the AI Assistant for {site_name}.\n"
                            "Your task: answer user questions ONLY based on the provided context documents.\n\n"
                            "IMPORTANT Rules:\n"
                            "1. Answer in the same language as the user's question.\n"
                            "2. If the answer is NOT in the context, honestly say: "
                            "'I'm sorry, but I couldn't find that information in our documentation. "
                            "Please contact the Support Desk for further assistance.'\n"
                            "3. DO NOT invent or hallucinate information not present in the context.\n"
                            "4. Provide concise, clear, and easy-to-understand answers.\n"
                            "5. If relevant, cite the document from which the information was derived.\n"
                        ),
                        verbose_name="System Prompt",
                    ),
                ),
                (
                    "ai_welcome_message",
                    models.TextField(
                        default=(
                            "Hello! I am an AI Assistant ready to help you. "
                            "Ask me anything about our Knowledge Base and User Manual."
                        ),
                        verbose_name="Welcome Message",
                    ),
                ),
                (
                    "ai_placeholder_text",
                    models.CharField(
                        default="Ask something...",
                        max_length=200,
                        verbose_name="Input Placeholder",
                    ),
                ),
                (
                    "ai_rate_limit_per_hour",
                    models.PositiveIntegerField(
                        default=30,
                        help_text="Maximum questions a single IP/session can ask per hour. Set to 0 to disable.",
                        verbose_name="Rate Limit (per hour)",
                    ),
                ),
            ],
            options={
                "verbose_name": "AI Assistant Configuration",
                "verbose_name_plural": "AI Assistant Configuration",
            },
        ),
    ]
