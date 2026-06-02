"""
E-Signature — PDF Stamping Utility
====================================
Stamps signature images onto an uploaded PDF at the positions defined by
SignaturePlacement records, then appends an audit certificate page.

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
"""
import io
from cases.utils_pdf import _format_dt_with_gmt


def _build_annotations(signer):
    """Return a list of annotation strings to print below the signer's signature."""
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

        # Absolute dimensions in PDF points
        abs_x = placement.x * page_width
        abs_w = placement.width * page_width
        abs_h = placement.height * page_height

        # Flip y: PDF y=0 is bottom; stored y is from top
        # abs_y_pdf = page_height - (abs_y_top + abs_h)
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
            # If the image fails to load, skip rather than crash the entire PDF
            continue

        # Annotation strip — drawn just below the signature box
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


def _build_certificate_page(document, page_width, page_height):
    """
    Return a BytesIO containing a single audit certificate page listing
    all signers, their timestamps, IPs, and the document hash.
    """
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors

    buf = io.BytesIO()
    pw, ph = page_width or A4[0], page_height or A4[1]
    c = canvas.Canvas(buf, pagesize=(pw, ph))

    margin = 50
    y = ph - margin

    # Title
    c.setFont("Helvetica-Bold", 14)
    c.setFillColor(colors.HexColor("#1e3a5f"))
    c.drawString(margin, y, "Document Signing Certificate")
    y -= 6

    c.setStrokeColor(colors.HexColor("#1e3a5f"))
    c.setLineWidth(1.5)
    c.line(margin, y, pw - margin, y)
    y -= 20

    # Document info
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

    # Signer list
    c.setFont("Helvetica-Bold", 10)
    c.setFillColor(colors.HexColor("#1e3a5f"))
    c.drawString(margin, y, "Signing History")
    y -= 14

    for signer in document.signers.order_by("order"):
        status_label = signer.get_status_display()
        acted = _format_dt_with_gmt(signer.acted_at) if signer.acted_at else "—"
        ip    = signer.signed_ip or "—"

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

    # Footer note
    c.setFont("Helvetica-Oblique", 7.5)
    c.setFillColor(colors.HexColor("#555555"))
    note = (
        "This certificate is automatically generated by the system upon completion of the "
        "signing workflow. The SHA-256 hash identifies the original, unmodified document. "
        "No physical signature is required — this document is legally valid as a digital record."
    )
    # Wrap long note
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


def stamp_document(document) -> bytes | None:
    """
    Main entry point. Reads the original PDF, stamps signature images at their
    defined placements, appends an audit certificate page, and returns the
    final PDF as bytes. Returns None if stamping fails.
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

        # Group placements by page number
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

            # Convert Decimal to float safely
            pw = float(mediabox.width)
            ph = float(mediabox.height)

            page_placements = placements_by_page.get(page_num, [])
            if page_placements:
                overlay_buf = _build_overlay(pw, ph, page_placements)
                overlay_reader = PdfReader(overlay_buf)
                overlay_page = overlay_reader.pages[0]
                page.merge_page(overlay_page)

            writer.add_page(page)

        # Append the audit certificate page
        cert_buf = _build_certificate_page(document, *A4)
        cert_reader = PdfReader(cert_buf)
        writer.add_page(cert_reader.pages[0])

        output = io.BytesIO()
        writer.write(output)
        output.seek(0)
        return output.read()

    except Exception:
        return None
