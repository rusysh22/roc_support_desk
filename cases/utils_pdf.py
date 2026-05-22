"""
PDF utilities for the cases app using WeasyPrint.
"""
import re
from io import BytesIO

from django.core.files.base import ContentFile
from django.utils import timezone


def render_document_html(body_html: str, filled_data: dict) -> str:
    """
    Replace {{placeholder}} tokens in body_html with values from filled_data.
    Returns a complete HTML string wrapped in a styled document shell.
    Used for admin DocumentTemplate preview/generation.
    """
    content = body_html
    for key, value in filled_data.items():
        content = content.replace(
            f"{{{{{key}}}}}",
            str(value) if value else f"<span style='color:#ef4444'>[{key}]</span>",
        )

    content = re.sub(
        r'\{\{(\w+)\}\}',
        lambda m: f"<span style='color:#ef4444'>[{m.group(1)}]</span>",
        content,
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<style>
  @page {{ size: A4; margin: 2.5cm 2cm; }}
  body {{ font-family: 'Times New Roman', Times, serif; font-size: 12pt; line-height: 1.6; color: #1a1a1a; }}
  .document-body {{ max-width: 100%; }}
  table {{ width: 100%; border-collapse: collapse; }}
  td, th {{ padding: 4px 8px; border: 1px solid #ccc; }}
</style>
</head>
<body>
<div class="document-body">
{content}
</div>
</body>
</html>"""


def generate_change_request_pdf(doc) -> bytes | None:
    """
    Render a ChangeRequestDocument as a PDF using WeasyPrint.
    Returns raw PDF bytes on success, None on failure.
    """
    try:
        import weasyprint
    except ImportError:
        return None

    requester_name = doc.case.requester_name or doc.case.requester_email or "Unknown"
    submitted_date = (doc.submitted_at or doc.created_at).strftime("%d %B %Y")
    revision_line = (
        f"<p><strong>Revision:</strong> {doc.revision_number}</p>"
        if doc.revision_number > 1 else ""
    )

    approved_html = ""
    approved_approvals = (
        doc.approvals.filter(status="approved")
        .order_by("order")
        .select_related("approver")
    )
    if approved_approvals.exists():
        blocks = []
        for approval in approved_approvals:
            name = approval.approver.get_full_name() or approval.approver.username
            acted = approval.acted_at.strftime("%d %B %Y") if approval.acted_at else ""
            blocks.append(
                f'<div style="text-align:center;width:200px;margin:0 20px;">'
                f'<div style="border-top:1px solid #333;margin-top:60px;padding-top:4px;">'
                f'<strong>{name}</strong><br>{acted}</div></div>'
            )
        approved_html = (
            '<div style="display:flex;justify-content:flex-end;gap:20px;'
            'flex-wrap:wrap;margin-top:40px;">'
            + "".join(blocks)
            + "</div>"
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8">
<style>
  @page {{ size: A4; margin: 2.5cm 2cm; }}
  body {{ font-family: 'Times New Roman', Times, serif; font-size: 12pt; line-height: 1.6; color: #1a1a1a; }}
  h1 {{ font-size: 14pt; text-align: center; text-transform: uppercase; margin-bottom: 16px; }}
  h2 {{ font-size: 12pt; margin-top: 20px; border-bottom: 1px solid #ccc; padding-bottom: 4px; }}
  .meta {{ margin: 12px 0; }}
  p {{ margin: 8px 0; white-space: pre-wrap; }}
</style>
</head>
<body>
<h1>Change Request Document</h1>
<div class="meta">
  <p><strong>Ticket:</strong> {doc.case.case_number}</p>
  <p><strong>Requester:</strong> {requester_name}</p>
  <p><strong>Date:</strong> {submitted_date}</p>
  {revision_line}
</div>
<hr>
<h2>Request Change</h2>
<p>{doc.request_change}</p>
<h2>Chronology</h2>
<p>{doc.chronology}</p>
{approved_html}
</body>
</html>"""

    try:
        return weasyprint.HTML(string=html).write_pdf()
    except Exception:
        return None
