"""
PDF utilities for the cases app using WeasyPrint.
"""
import base64
import re

from django.utils import timezone as tz


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


def _format_dt_with_gmt(dt) -> str:
    """Return a datetime string with local offset, e.g. '23 Mei 2025, 14:30 (GMT+07:00)'."""
    local = tz.localtime(dt)
    offset = local.strftime("%z")          # e.g. "+0700" or "-0500"
    gmt = f"GMT{offset[:3]}:{offset[3:]}" # e.g. "GMT+07:00"
    return local.strftime("%d %B %Y, %H:%M") + f" ({gmt})"


def _logo_data_uri(site_config) -> str:
    """Return a base64 data-URI for the site logo, or empty string if unavailable."""
    if not site_config or not site_config.logo:
        return ""
    try:
        with open(site_config.logo.path, "rb") as f:
            raw = base64.b64encode(f.read()).decode("utf-8")
        name = site_config.logo.name.lower()
        if name.endswith(".svg"):
            mime = "image/svg+xml"
        elif name.endswith(".png"):
            mime = "image/png"
        else:
            mime = "image/jpeg"
        return f"data:{mime};base64,{raw}"
    except Exception:
        return ""


def generate_change_request_pdf(doc) -> bytes | None:
    """
    Render a ChangeRequestDocument as a formatted A4 PDF using WeasyPrint.

    Includes:
    - Letterhead (kop surat) with site logo and site name
    - Requester identity: name, unit, email, job role
    - Submission date with GMT offset
    - Request Change and Chronology content
    - Approver signature blocks with date, time, and GMT offset
    """
    try:
        import weasyprint
    except ImportError:
        return None

    from core.models import SiteConfig

    site_config = SiteConfig.get_solo()
    site_name = site_config.site_name if site_config else "Support Desk"
    logo_uri = _logo_data_uri(site_config)

    case = doc.case
    requester_name  = case.requester_name or "—"
    requester_email = case.requester_email or "—"
    requester_unit  = case.requester_unit_name or "—"
    requester_role  = case.requester_job_role or "—"

    submitted_str = _format_dt_with_gmt(doc.submitted_at or doc.created_at)

    revision_row = (
        f"<tr><td class='lbl'>Nomor Revisi</td><td class='sep'>:</td>"
        f"<td>#{doc.revision_number}</td></tr>"
        if doc.revision_number > 1 else ""
    )

    # Approver signature blocks — only those who approved
    approved_approvals = (
        doc.approvals.filter(status="approved")
        .order_by("order")
        .select_related("approver")
    )
    sig_blocks = ""
    if approved_approvals.exists():
        blocks = []
        for approval in approved_approvals:
            name = approval.approver.get_full_name() or approval.approver.username
            if approval.acted_at:
                act_str = _format_dt_with_gmt(approval.acted_at)
            else:
                act_str = "—"
            blocks.append(
                f'<div class="sig-block">'
                f'<div class="sig-space"></div>'
                f'<div class="sig-line"></div>'
                f'<strong>{name}</strong>'
                f'<span class="sig-date">{act_str}</span>'
                f'</div>'
            )
        sig_blocks = f'<div class="sig-row">{"".join(blocks)}</div>'

    logo_tag = (
        f'<img src="{logo_uri}" class="logo" alt="{site_name}">'
        if logo_uri else ""
    )

    html = f"""<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="UTF-8">
<style>
  @page {{ size: A4; margin: 2.5cm 2cm; }}
  body {{
    font-family: 'Times New Roman', Times, serif;
    font-size: 11pt;
    line-height: 1.65;
    color: #1a1a1a;
    margin: 0;
  }}

  /* ── Letterhead ── */
  .kop {{
    display: flex;
    align-items: center;
    gap: 18px;
    border-bottom: 3px solid #1e3a5f;
    padding-bottom: 10px;
    margin-bottom: 18px;
  }}
  .logo {{ height: 56px; max-width: 110px; object-fit: contain; }}
  .kop-text {{ flex: 1; }}
  .kop-name {{
    font-size: 15pt;
    font-weight: bold;
    color: #1e3a5f;
    margin: 0;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }}
  .kop-sub {{
    font-size: 9pt;
    color: #555;
    margin: 2px 0 0;
  }}

  /* ── Document title ── */
  .doc-title {{
    text-align: center;
    margin: 16px 0 14px;
  }}
  .doc-title h1 {{
    font-size: 13pt;
    font-weight: bold;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    border-bottom: 1.5px solid #1e3a5f;
    display: inline-block;
    padding-bottom: 4px;
    margin: 0;
  }}

  /* ── Meta table ── */
  .meta {{ width: 100%; border-collapse: collapse; margin-bottom: 18px; }}
  .meta td {{ padding: 2px 6px; font-size: 10.5pt; vertical-align: top; }}
  .meta .lbl {{ width: 38%; font-weight: bold; color: #333; }}
  .meta .sep {{ width: 3%; }}

  hr {{ border: none; border-top: 1px solid #ccc; margin: 14px 0; }}

  /* ── Content sections ── */
  h2 {{
    font-size: 10.5pt;
    font-weight: bold;
    text-transform: uppercase;
    letter-spacing: 0.4px;
    color: #1e3a5f;
    border-bottom: 1px solid #ddd;
    padding-bottom: 3px;
    margin: 18px 0 8px;
  }}
  .content-text {{
    font-size: 11pt;
    white-space: pre-wrap;
    margin: 0;
  }}

  /* ── Signatures ── */
  .sig-row {{
    display: flex;
    justify-content: flex-end;
    gap: 40px;
    flex-wrap: wrap;
    margin-top: 50px;
    page-break-inside: avoid;
  }}
  .sig-block {{
    text-align: center;
    min-width: 160px;
  }}
  .sig-space {{ height: 60px; }}
  .sig-line {{ border-top: 1px solid #333; margin-bottom: 4px; }}
  .sig-block strong {{ display: block; font-size: 10.5pt; }}
  .sig-date {{ display: block; font-size: 9pt; color: #555; margin-top: 2px; }}
</style>
</head>
<body>

<!-- Kop Surat -->
<div class="kop">
  {logo_tag}
  <div class="kop-text">
    <p class="kop-name">{site_name}</p>
    <p class="kop-sub">Surat Kronologi / Change Request Document</p>
  </div>
</div>

<!-- Judul Dokumen -->
<div class="doc-title">
  <h1>Surat Kronologi / Change Request</h1>
</div>

<!-- Identitas Pemohon -->
<table class="meta">
  <tr><td class="lbl">No. Tiket</td><td class="sep">:</td><td>{case.case_number}</td></tr>
  <tr><td class="lbl">Nama Pemohon</td><td class="sep">:</td><td>{requester_name}</td></tr>
  <tr><td class="lbl">Unit / Departemen</td><td class="sep">:</td><td>{requester_unit}</td></tr>
  <tr><td class="lbl">Email</td><td class="sep">:</td><td>{requester_email}</td></tr>
  <tr><td class="lbl">Jabatan</td><td class="sep">:</td><td>{requester_role}</td></tr>
  <tr><td class="lbl">Tanggal Pengajuan</td><td class="sep">:</td><td>{submitted_str}</td></tr>
  {revision_row}
</table>

<hr>

<!-- Isi Dokumen -->
<h2>Permintaan Perubahan</h2>
<p class="content-text">{doc.request_change}</p>

<h2>Kronologi</h2>
<p class="content-text">{doc.chronology}</p>

<!-- Tanda Tangan Approver -->
{sig_blocks}

</body>
</html>"""

    try:
        return weasyprint.HTML(string=html).write_pdf()
    except Exception:
        return None
