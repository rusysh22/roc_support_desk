from django.db import models
from core.models import AuditableModel

class SharedDocument(AuditableModel):
    """
    A standalone HTML/Markdown document published via a unique public URL.
    """
    class EditorMode(models.TextChoices):
        RICH_TEXT = "RICH_TEXT", "Rich Text (Quill)"
        RAW_HTML  = "RAW_HTML",  "Raw HTML/CSS Code"

    title = models.CharField(
        max_length=255,
        verbose_name="Title",
        help_text="Document title (e.g. D365 Architecture Overview)",
    )
    editor_mode = models.CharField(
        max_length=20,
        choices=EditorMode.choices,
        default=EditorMode.RICH_TEXT,
        verbose_name="Editor Mode",
        help_text="Choose Raw HTML to embed a complete custom HTML/CSS document.",
    )
    content = models.TextField(
        verbose_name="Content",
        help_text="HTML or Markdown content — will be sanitized before display.",
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="Active",
        help_text="Disable to revoke public access without deleting the document.",
    )

    class Meta:
        verbose_name = "Shared Document"
        verbose_name_plural = "Shared Documents"
        ordering = ["-created_at"]
        db_table = "projects_shareddocument"

    def __str__(self):
        return self.title
