"""
PDF utilities for CaseDocument generation using WeasyPrint.
"""
import re
from io import BytesIO

from django.core.files.base import ContentFile
from django.utils import timezone


def render_document_html(body_html: str, filled_data: dict) -> str:
    """
    Replace {{placeholder}} tokens in body_html with values from filled_data.
    Returns a complete HTML string wrapped in a styled document shell.
    """
    content = body_html
    for key, value in filled_data.items():
        content = content.replace(f"{{{{{key}}}}}", str(value) if value else f"<span style='color:#ef4444'>[{key}]</span>")

    # Replace any remaining unfilled placeholders with a red indicator
    content = re.sub(
        r'\{\{(\w+)\}\}',
        lambda m: f"<span style='color:#ef4444'>[{m.group(1)}]</span>",
        content,
    )

    return f"""<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="UTF-8">
<style>
  @page {{
    size: A4;
    margin: 2.5cm 2cm;
  }}
  body {{
    font-family: 'Times New Roman', Times, serif;
    font-size: 12pt;
    line-height: 1.6;
    color: #1a1a1a;
  }}
  .document-body {{
    max-width: 100%;
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
  }}
  td, th {{
    padding: 4px 8px;
    border: 1px solid #ccc;
  }}
  .signature-block {{
    margin-top: 40px;
    display: flex;
    justify-content: flex-end;
  }}
  .signature-cell {{
    text-align: center;
    width: 200px;
  }}
  .signature-img {{
    max-width: 180px;
    max-height: 80px;
    display: block;
    margin: 0 auto;
  }}
  .signature-line {{
    border-top: 1px solid #333;
    margin-top: 60px;
    padding-top: 4px;
    font-size: 10pt;
  }}
</style>
</head>
<body>
<div class="document-body">
{content}
</div>
</body>
</html>"""


def _build_signature_html(doc) -> str:
    """Build the signature blocks section to append to the document."""
    approved_steps = doc.approval_steps.filter(
        status="approved"
    ).order_by("order").select_related("approver")

    if not approved_steps.exists():
        return ""

    blocks = []
    for step in approved_steps:
        name = step.approver.get_full_name() or step.approver.username
        acted = ""
        if step.acted_at:
            acted = step.acted_at.strftime("%d %B %Y")

        sig_img = ""
        if step.signature_image:
            try:
                import base64
                img_data = step.signature_image.read()
                b64 = base64.b64encode(img_data).decode("utf-8")
                sig_img = f'<img class="signature-img" src="data:image/png;base64,{b64}" />'
            except Exception:
                pass

        blocks.append(f"""
        <div class="signature-cell">
            {sig_img}
            <div class="signature-line">
                <strong>{name}</strong><br>
                {acted}
            </div>
        </div>
        """)

    return f"""
    <div class="signature-block" style="margin-top:40px; display:flex; gap:40px; justify-content:flex-end; flex-wrap:wrap;">
        {"".join(blocks)}
    </div>
    """


def generate_case_document_pdf(doc) -> bool:
    """
    Generate a PDF from a CaseDocument using WeasyPrint.
    Saves the result to doc.generated_pdf and returns True on success.
    """
    try:
        import weasyprint
    except ImportError:
        return False

    body_html = render_document_html(doc.template.body_html, doc.filled_data)
    signature_html = _build_signature_html(doc)

    # Inject signature blocks before closing body tag
    full_html = body_html.replace("</div>\n</body>", f"{signature_html}</div>\n</body>")

    try:
        pdf_bytes = weasyprint.HTML(string=full_html).write_pdf()
    except Exception:
        return False

    filename = f"document_{str(doc.id)[:8]}.pdf"
    doc.generated_pdf.save(filename, ContentFile(pdf_bytes), save=True)
    return True
