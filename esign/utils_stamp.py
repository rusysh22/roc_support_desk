"""
E-Signature — PDF Stamping Utility
====================================
Stamps signature images onto an uploaded PDF at the positions defined by
SignaturePlacement records, then optionally appends an audit certificate page.

Libraries used:
  pypdf      — read page mediaboxes, merge overlay pages onto originals.
  reportlab  — build signature overlay canvases and the certificate page.
  Pillow     — already a project dependency; used for image validation.

Coordinate system note:
  - The browser placement widget uses a top-left origin (CSS convention).
  - PDF coordinate origin is bottom-left.
  - Stored x/y are normalized fractions (0.0–1.0) relative to page width/height
    as measured from the TOP-LEFT.  This function flips the y-axis when placing
    images onto the reportlab canvas (which uses bottom-left origin).

Certificate styles:
  none       — no certificate appended (default).
  page       — full audit certificate page with detailed signer history.
  compact    — clean minimal summary box at the top of a new page.
  logo_stamp — formal page with company logo seal and signing table.
"""
import io
from cases.utils_pdf import _format_dt_with_gmt


# ---------------------------------------------------------------------------
# Signature overlay helpers
# ---------------------------------------------------------------------------

def _build_annotations(signer):
    """Return annotation strings to print below the signer's signature box."""
    parts = []
    if getattr(signer, "stamp_name", True):
        parts.append(signer.display_name)
    if getattr(signer, "stamp_job_role", False) and signer.stamp_job_role_text:
        parts.append(signer.stamp_job_role_text)
    if getattr(signer, "stamp_timestamp", True) and signer.acted_at:
        parts.append(signer.acted_at.strftime("%d %b %Y %H:%M"))
    return parts


def _build_overlay(page_width, page_height, placements_for_page):
    """
    Return a BytesIO containing a single-page PDF overlay with all signature
    images stamped at their specified positions.
    """
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(page_width, page_height))

    for placement, signer in placements_for_page:
        if not signer.signature_image:
            continue

        abs_x = placement.x * page_width
        abs_w = placement.width * page_width
        abs_h = placement.height * page_height

        # Flip y: PDF origin is bottom-left; stored y is from top-left
        abs_y_top = placement.y * page_height
        abs_y_pdf = page_height - abs_y_top - abs_h

        try:
            signer.signature_image.seek(0)
            img_reader = ImageReader(signer.signature_image)
            c.drawImage(
                img_reader,
                abs_x, abs_y_pdf, abs_w, abs_h,
                mask="auto",
                preserveAspectRatio=True,
            )
        except Exception:
            continue

        annotations = _build_annotations(signer)
        if annotations:
            from reportlab.lib import colors as rl_colors
            c.setLineWidth(0.4)
            c.setStrokeColor(rl_colors.HexColor("#94a3b8"))
            c.line(abs_x, abs_y_pdf, abs_x + abs_w, abs_y_pdf)
            c.setFont("Helvetica", 6.5)
            c.setFillColor(rl_colors.HexColor("#374151"))
            ann_y = abs_y_pdf - 9
            for text in annotations:
                c.drawString(abs_x + 2, ann_y, text[:70])
                ann_y -= 8

    c.save()
    buf.seek(0)
    return buf


# ---------------------------------------------------------------------------
# Certificate style: "page" — full detailed audit page
# ---------------------------------------------------------------------------

def _build_certificate_page(document, page_width, page_height):
    """Full audit certificate: document info, signer history, SHA-256 hash."""
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors

    buf = io.BytesIO()
    pw, ph = page_width or A4[0], page_height or A4[1]
    c = canvas.Canvas(buf, pagesize=(pw, ph))

    margin = 50
    y = ph - margin

    c.setFont("Helvetica-Bold", 14)
    c.setFillColor(colors.HexColor("#1e3a5f"))
    c.drawString(margin, y, "Document Signing Certificate")
    y -= 6
    c.setStrokeColor(colors.HexColor("#1e3a5f"))
    c.setLineWidth(1.5)
    c.line(margin, y, pw - margin, y)
    y -= 20

    c.setFont("Helvetica-Bold", 9)
    c.setFillColor(colors.black)
    c.drawString(margin, y, "Document Title:")
    c.setFont("Helvetica", 9)
    c.drawString(margin + 100, y, document.title)
    y -= 14

    c.setFont("Helvetica-Bold", 9)
    c.drawString(margin, y, "SHA-256 Hash:")
    c.setFont("Helvetica", 8)
    c.drawString(margin + 100, y, document.document_hash or "—")
    y -= 14

    completed_event = (
        document.events.filter(event="completed").order_by("-created_at").first()
    )
    if completed_event:
        c.setFont("Helvetica-Bold", 9)
        c.drawString(margin, y, "Completed At:")
        c.setFont("Helvetica", 9)
        c.drawString(margin + 100, y, _format_dt_with_gmt(completed_event.created_at))
        y -= 14

    y -= 10
    c.setStrokeColor(colors.HexColor("#cccccc"))
    c.setLineWidth(0.5)
    c.line(margin, y, pw - margin, y)
    y -= 16

    c.setFont("Helvetica-Bold", 10)
    c.setFillColor(colors.HexColor("#1e3a5f"))
    c.drawString(margin, y, "Signing History")
    y -= 14

    for signer in document.signers.order_by("order"):
        status_label = signer.get_status_display()
        acted = _format_dt_with_gmt(signer.acted_at) if signer.acted_at else "—"
        ip = signer.signed_ip or "—"

        c.setFont("Helvetica-Bold", 9)
        c.setFillColor(colors.black)
        c.drawString(margin, y, f"{signer.order}. {signer.display_name}")
        c.setFont("Helvetica", 8)
        c.setFillColor(colors.HexColor("#444444"))
        c.drawString(margin + 10, y - 12, f"Email: {signer.email}")
        c.drawString(margin + 10, y - 22, f"Status: {status_label}  |  Date: {acted}  |  IP: {ip}")
        if signer.notes:
            c.setFillColor(colors.HexColor("#b91c1c"))
            c.drawString(margin + 10, y - 32, f"Notes: {signer.notes[:120]}")
            y -= 44
        else:
            y -= 36

        if y < margin + 40:
            c.showPage()
            y = ph - margin

    y -= 10
    c.setStrokeColor(colors.HexColor("#cccccc"))
    c.line(margin, y, pw - margin, y)
    y -= 14

    c.setFont("Helvetica-Oblique", 7.5)
    c.setFillColor(colors.HexColor("#555555"))
    note = (
        "This certificate is automatically generated by the system upon completion of the "
        "signing workflow. The SHA-256 hash identifies the original, unmodified document. "
        "No physical signature is required — this document is legally valid as a digital record."
    )
    words = note.split()
    line, lines = "", []
    for word in words:
        test = f"{line} {word}".strip()
        if c.stringWidth(test, "Helvetica-Oblique", 7.5) < (pw - 2 * margin):
            line = test
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    for text_line in lines:
        c.drawString(margin, y, text_line)
        y -= 10

    c.save()
    buf.seek(0)
    return buf


# ---------------------------------------------------------------------------
# Certificate style: "compact" — minimal summary box
# ---------------------------------------------------------------------------

def _build_compact_certificate(document, page_width, page_height):
    """Compact signing summary: essential info only in a clean bordered box."""
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors

    buf = io.BytesIO()
    pw, ph = page_width or A4[0], page_height or A4[1]
    c = canvas.Canvas(buf, pagesize=(pw, ph))

    margin = 48
    box_x = margin
    box_w = pw - 2 * margin

    completed_event = (
        document.events.filter(event="completed").order_by("-created_at").first()
    )
    completed_at = (
        _format_dt_with_gmt(completed_event.created_at) if completed_event else "—"
    )

    signers = list(document.signers.order_by("order"))
    # Estimate box height: header + doc info + divider + each signer row + footer
    row_h = 18
    box_h = 28 + 14 + 14 + 14 + 10 + (len(signers) * row_h) + 10 + 16
    box_y = ph - margin - box_h

    # Outer box
    c.setStrokeColor(colors.HexColor("#cbd5e1"))
    c.setLineWidth(0.8)
    c.roundRect(box_x, box_y, box_w, box_h, 6, stroke=1, fill=0)

    # Header strip
    c.setFillColor(colors.HexColor("#f1f5f9"))
    c.roundRect(box_x, box_y + box_h - 28, box_w, 28, 6, stroke=0, fill=1)
    # Cover bottom corners of header so they aren't rounded
    c.rect(box_x, box_y + box_h - 28, box_w, 14, stroke=0, fill=1)

    c.setFont("Helvetica-Bold", 9)
    c.setFillColor(colors.HexColor("#334155"))
    c.drawString(box_x + 12, box_y + box_h - 18, "Digital Signing Certificate")

    # Check icon text
    c.setFont("Helvetica", 8)
    c.setFillColor(colors.HexColor("#16a34a"))
    c.drawRightString(box_x + box_w - 12, box_y + box_h - 18, "✓ Verified")

    inner_y = box_y + box_h - 38

    # Document info
    c.setFont("Helvetica-Bold", 7.5)
    c.setFillColor(colors.HexColor("#64748b"))
    c.drawString(box_x + 12, inner_y, "DOCUMENT")
    c.setFont("Helvetica", 8)
    c.setFillColor(colors.HexColor("#1e293b"))
    c.drawString(box_x + 70, inner_y, document.title[:80])
    inner_y -= 13

    c.setFont("Helvetica-Bold", 7.5)
    c.setFillColor(colors.HexColor("#64748b"))
    c.drawString(box_x + 12, inner_y, "COMPLETED")
    c.setFont("Helvetica", 8)
    c.setFillColor(colors.HexColor("#1e293b"))
    c.drawString(box_x + 70, inner_y, completed_at)
    inner_y -= 13

    hash_display = document.document_hash[:32] + "…" if document.document_hash else "—"
    c.setFont("Helvetica-Bold", 7.5)
    c.setFillColor(colors.HexColor("#64748b"))
    c.drawString(box_x + 12, inner_y, "SHA-256")
    c.setFont("Helvetica", 7.5)
    c.setFillColor(colors.HexColor("#475569"))
    c.drawString(box_x + 70, inner_y, hash_display)
    inner_y -= 14

    # Divider
    c.setStrokeColor(colors.HexColor("#e2e8f0"))
    c.setLineWidth(0.5)
    c.line(box_x + 12, inner_y, box_x + box_w - 12, inner_y)
    inner_y -= 12

    # Signer rows
    for signer in signers:
        acted = signer.acted_at.strftime("%d %b %Y %H:%M") if signer.acted_at else "—"
        is_signed = signer.status == "signed"

        c.setFont("Helvetica-Bold", 7.5)
        c.setFillColor(colors.HexColor("#16a34a") if is_signed else colors.HexColor("#94a3b8"))
        c.drawString(box_x + 12, inner_y, "✓" if is_signed else "○")

        c.setFont("Helvetica", 8)
        c.setFillColor(colors.HexColor("#1e293b"))
        c.drawString(box_x + 24, inner_y, signer.display_name[:35])

        c.setFont("Helvetica", 7.5)
        c.setFillColor(colors.HexColor("#64748b"))
        c.drawString(box_x + 160, inner_y, signer.email[:35])
        c.drawRightString(box_x + box_w - 12, inner_y, acted)
        inner_y -= row_h

    # Footer
    inner_y -= 2
    c.setStrokeColor(colors.HexColor("#e2e8f0"))
    c.line(box_x + 12, inner_y, box_x + box_w - 12, inner_y)
    inner_y -= 10
    c.setFont("Helvetica-Oblique", 6.5)
    c.setFillColor(colors.HexColor("#94a3b8"))
    c.drawString(
        box_x + 12, inner_y,
        "Generated automatically upon completion of the signing workflow.",
    )

    c.save()
    buf.seek(0)
    return buf


# ---------------------------------------------------------------------------
# Certificate style: "logo_stamp" — formal page with company logo
# ---------------------------------------------------------------------------

def _build_logo_stamp_certificate(document, page_width, page_height):
    """Formal certificate with company logo seal, signing table, and hash."""
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.utils import ImageReader

    buf = io.BytesIO()
    pw, ph = page_width or A4[0], page_height or A4[1]
    c = canvas.Canvas(buf, pagesize=(pw, ph))

    margin = 50
    y = ph - margin

    # Fetch site config for logo / site name
    site_name = "E-Sign System"
    logo_reader = None
    try:
        from core.models import SiteConfig
        cfg = SiteConfig.objects.first()
        if cfg:
            site_name = cfg.site_name or site_name
            if cfg.logo:
                cfg.logo.seek(0)
                logo_reader = ImageReader(cfg.logo)
    except Exception:
        pass

    # Header background band
    band_h = 80
    c.setFillColor(colors.HexColor("#1e3a5f"))
    c.rect(0, ph - band_h, pw, band_h, stroke=0, fill=1)

    # Logo in header (left-aligned) or site name text
    if logo_reader:
        try:
            logo_size = 50
            c.drawImage(
                logo_reader,
                margin, ph - band_h + 15,
                width=logo_size, height=logo_size,
                mask="auto", preserveAspectRatio=True,
            )
            text_x = margin + logo_size + 12
        except Exception:
            text_x = margin
    else:
        text_x = margin

    c.setFont("Helvetica-Bold", 16)
    c.setFillColor(colors.white)
    c.drawString(text_x, ph - 38, site_name)
    c.setFont("Helvetica", 9)
    c.setFillColor(colors.HexColor("#93c5fd"))
    c.drawString(text_x, ph - 54, "Official Digital Signing Certificate")

    # Completion badge (right side of header)
    c.setFont("Helvetica-Bold", 8)
    c.setFillColor(colors.HexColor("#22c55e"))
    c.drawRightString(pw - margin, ph - 40, "✓ COMPLETED")
    c.setFont("Helvetica", 7.5)
    c.setFillColor(colors.HexColor("#93c5fd"))
    completed_event = (
        document.events.filter(event="completed").order_by("-created_at").first()
    )
    completed_at = (
        _format_dt_with_gmt(completed_event.created_at) if completed_event else "—"
    )
    c.drawRightString(pw - margin, ph - 52, completed_at)

    y = ph - band_h - 28

    # Document info block
    c.setFont("Helvetica-Bold", 12)
    c.setFillColor(colors.HexColor("#1e293b"))
    c.drawString(margin, y, document.title)
    y -= 16

    c.setFont("Helvetica", 8)
    c.setFillColor(colors.HexColor("#64748b"))
    c.drawString(margin, y, f"SHA-256: {document.document_hash or '—'}")
    y -= 24

    # Divider
    c.setStrokeColor(colors.HexColor("#e2e8f0"))
    c.setLineWidth(0.8)
    c.line(margin, y, pw - margin, y)
    y -= 16

    # Signing history header
    c.setFont("Helvetica-Bold", 9)
    c.setFillColor(colors.HexColor("#334155"))
    c.drawString(margin, y, "Signing History")
    y -= 12

    # Table header row
    col_name  = margin
    col_email = margin + 145
    col_date  = margin + 310
    col_ip    = margin + 420

    c.setFillColor(colors.HexColor("#f8fafc"))
    c.rect(margin, y - 4, pw - 2 * margin, 16, stroke=0, fill=1)
    c.setFont("Helvetica-Bold", 7.5)
    c.setFillColor(colors.HexColor("#475569"))
    c.drawString(col_name,  y + 4, "Name")
    c.drawString(col_email, y + 4, "Email")
    c.drawString(col_date,  y + 4, "Signed At")
    c.drawString(col_ip,    y + 4, "IP Address")
    y -= 16

    c.setStrokeColor(colors.HexColor("#e2e8f0"))
    c.setLineWidth(0.4)
    c.line(margin, y, pw - margin, y)
    y -= 3

    # Signer rows
    for signer in document.signers.order_by("order"):
        is_signed = signer.status == "signed"
        acted = signer.acted_at.strftime("%d %b %Y %H:%M") if signer.acted_at else "—"
        ip = signer.signed_ip or "—"

        row_color = colors.HexColor("#f0fdf4") if is_signed else colors.HexColor("#fff7ed")
        c.setFillColor(row_color)
        c.rect(margin, y - 6, pw - 2 * margin, 17, stroke=0, fill=1)

        status_dot = "●" if is_signed else "○"
        dot_color = colors.HexColor("#16a34a") if is_signed else colors.HexColor("#f59e0b")

        c.setFont("Helvetica", 7)
        c.setFillColor(dot_color)
        c.drawString(col_name, y + 4, status_dot)

        c.setFont("Helvetica-Bold", 8)
        c.setFillColor(colors.HexColor("#1e293b"))
        c.drawString(col_name + 10, y + 4, signer.display_name[:25])

        c.setFont("Helvetica", 7.5)
        c.setFillColor(colors.HexColor("#475569"))
        c.drawString(col_email, y + 4, signer.email[:30])
        c.drawString(col_date,  y + 4, acted)
        c.drawString(col_ip,    y + 4, ip)

        if signer.notes:
            y -= 17
            c.setFont("Helvetica-Oblique", 7)
            c.setFillColor(colors.HexColor("#b91c1c"))
            c.drawString(col_name + 10, y + 4, f"Rejected: {signer.notes[:90]}")

        y -= 17

        c.setStrokeColor(colors.HexColor("#e2e8f0"))
        c.setLineWidth(0.3)
        c.line(margin, y, pw - margin, y)

        if y < margin + 40:
            c.showPage()
            y = ph - margin

    y -= 20

    # Footer
    c.setStrokeColor(colors.HexColor("#cbd5e1"))
    c.setLineWidth(0.5)
    c.line(margin, y, pw - margin, y)
    y -= 12

    c.setFont("Helvetica-Oblique", 7)
    c.setFillColor(colors.HexColor("#94a3b8"))
    footer = (
        "This certificate was generated automatically upon completion of the digital signing workflow. "
        "The SHA-256 hash above uniquely identifies the original, unmodified document."
    )
    words = footer.split()
    line, lines = "", []
    for word in words:
        test = f"{line} {word}".strip()
        if c.stringWidth(test, "Helvetica-Oblique", 7) < (pw - 2 * margin):
            line = test
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    for text_line in lines:
        c.drawString(margin, y, text_line)
        y -= 9

    c.save()
    buf.seek(0)
    return buf


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def stamp_document(document) -> bytes | None:
    """
    Read the original PDF, stamp signatures at their placements, optionally
    append a certificate page based on document.certificate_style, and return
    the final PDF as bytes. Returns None if stamping fails.
    """
    try:
        from pypdf import PdfReader, PdfWriter
        from reportlab.lib.pagesizes import A4
    except ImportError:
        return None

    try:
        document.original_pdf.seek(0)
        reader = PdfReader(document.original_pdf)
        writer = PdfWriter()

        placements = list(
            document.placements.filter(
                signer__status="signed"
            ).select_related("signer")
        )

        placements_by_page: dict[int, list] = {}
        for p in placements:
            placements_by_page.setdefault(p.page_number, []).append((p, p.signer))

        for page_index, page in enumerate(reader.pages):
            page_num = page_index + 1
            mediabox = page.mediabox
            pw = float(mediabox.width)
            ph = float(mediabox.height)

            page_placements = placements_by_page.get(page_num, [])
            if page_placements:
                overlay_buf = _build_overlay(pw, ph, page_placements)
                overlay_reader = PdfReader(overlay_buf)
                page.merge_page(overlay_reader.pages[0])

            writer.add_page(page)

        # Append certificate based on chosen style
        cert_style = getattr(document, "certificate_style", "none")
        cert_builders = {
            "page":       _build_certificate_page,
            "compact":    _build_compact_certificate,
            "logo_stamp": _build_logo_stamp_certificate,
        }
        builder = cert_builders.get(cert_style)
        if builder:
            cert_buf = builder(document, *A4)
            cert_reader = PdfReader(cert_buf)
            for cert_page in cert_reader.pages:
                writer.add_page(cert_page)

        output = io.BytesIO()
        writer.write(output)
        output.seek(0)
        return output.read()

    except Exception:
        return None
