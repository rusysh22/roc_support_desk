"""
E-Signature — PDF Stamping Utility
====================================
Stamps signature images onto an uploaded PDF at the positions defined by
SignaturePlacement records.

Stamp styles (document.certificate_style):
  simple  — signature image with optional text annotations below the box.
  branded — signature image embedded inside a styled stamp frame:
             top header band (site logo + "Digitally Signed"), signature in
             the middle, signer info in a footer strip — all within the
             placement box boundaries.

Libraries:
  pypdf      — read page mediaboxes, merge overlay pages onto originals.
  reportlab  — build signature overlay canvases.
  Pillow     — used for image validation (already a project dependency).

Coordinate system note:
  - The browser placement widget uses a top-left origin (CSS convention).
  - PDF coordinate origin is bottom-left.
  - Stored x/y are normalized fractions (0.0–1.0) relative to page size
    measured from the TOP-LEFT.  This function flips the y-axis when
    placing images onto the reportlab canvas (bottom-left origin).
"""
import io
import logging
from cases.utils_pdf import _format_dt_with_gmt

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Annotation text helpers
# ---------------------------------------------------------------------------

def _build_annotations(signer):
    """Return annotation strings to stamp on the signature."""
    parts = []
    if getattr(signer, "stamp_name", True):
        parts.append(signer.display_name)
    if getattr(signer, "stamp_job_role", False) and signer.stamp_job_role_text:
        parts.append(signer.stamp_job_role_text)
    if getattr(signer, "stamp_timestamp", True) and signer.acted_at:
        parts.append(signer.acted_at.strftime("%d %b %Y %H:%M"))
    return parts


def _fetch_site_branding():
    """Return (site_name, logo_reader) from SiteConfig. Never raises."""
    site_name = "E-Sign"
    logo_reader = None
    try:
        from core.models import SiteConfig
        from reportlab.lib.utils import ImageReader
        cfg = SiteConfig.objects.first()
        if cfg:
            site_name = (cfg.site_name or site_name)[:30]
            if cfg.logo:
                cfg.logo.seek(0)
                logo_reader = ImageReader(cfg.logo)
    except Exception:
        pass
    return site_name, logo_reader


# ---------------------------------------------------------------------------
# Stamp style: "simple" — signature image + text below the box
# ---------------------------------------------------------------------------

def _stamp_simple(c, abs_x, abs_y_pdf, abs_w, abs_h, signer, annotations):
    """Draw signature image then optional annotation text beneath the box."""
    from reportlab.lib.utils import ImageReader
    from reportlab.lib import colors as rl_colors

    if not signer.signature_image:
        return

    try:
        with signer.signature_image.open('rb') as f:
            img_bytes = io.BytesIO(f.read())
        img_reader = ImageReader(img_bytes)
        c.drawImage(
            img_reader,
            abs_x, abs_y_pdf, abs_w, abs_h,
            mask="auto",
            preserveAspectRatio=True,
        )
    except Exception:
        return

    if annotations:
        c.setLineWidth(0.4)
        c.setStrokeColor(rl_colors.HexColor("#94a3b8"))
        c.line(abs_x, abs_y_pdf, abs_x + abs_w, abs_y_pdf)
        c.setFont("Helvetica", 6.5)
        c.setFillColor(rl_colors.HexColor("#374151"))
        ann_y = abs_y_pdf - 9
        for text in annotations:
            c.drawString(abs_x + 2, ann_y, text[:70])
            ann_y -= 8


# ---------------------------------------------------------------------------
# Stamp style: "branded" — styled stamp frame with logo, all inside the box
# ---------------------------------------------------------------------------

def _stamp_branded(c, abs_x, abs_y_pdf, abs_w, abs_h, signer, annotations,
                   site_name="E-Sign", logo_reader=None):
    """
    Draw a branded stamp frame within the placement box boundaries:
    - Dark header band at top: site logo + "Digitally Signed" label
    - Signature image in the middle (white background)
    - Light footer strip at bottom: signer name + date
    """
    from reportlab.lib import colors as rl_colors
    from reportlab.lib.utils import ImageReader

    radius = min(3, abs_h * 0.05)
    indigo  = rl_colors.HexColor("#4338ca")
    white   = rl_colors.white
    surface = rl_colors.HexColor("#f8fafc")
    text_dk = rl_colors.HexColor("#1e293b")
    text_md = rl_colors.HexColor("#475569")

    # Proportional heights
    header_h = max(10, abs_h * 0.20)
    footer_h = max(9,  abs_h * 0.21) if annotations else 0
    sig_h    = abs_h - header_h - footer_h

    header_y = abs_y_pdf + abs_h - header_h
    sig_y    = abs_y_pdf + footer_h

    # ── Outer border ──────────────────────────────────────────────────────
    c.setStrokeColor(indigo)
    c.setLineWidth(0.7)
    c.roundRect(abs_x, abs_y_pdf, abs_w, abs_h, radius, stroke=1, fill=0)

    # ── Header band (dark indigo) ─────────────────────────────────────────
    c.setFillColor(indigo)
    c.roundRect(abs_x, header_y, abs_w, header_h, radius, stroke=0, fill=1)
    # Cover bottom corners of the rounded rect so they don't bleed into sig area
    c.rect(abs_x, header_y, abs_w, header_h * 0.5, stroke=0, fill=1)

    # Logo in header (left side)
    logo_w = header_h * 0.75
    text_x = abs_x + 4
    if logo_reader:
        try:
            c.drawImage(
                logo_reader,
                abs_x + 2, header_y + header_h * 0.1,
                width=logo_w, height=header_h * 0.8,
                mask="auto", preserveAspectRatio=True,
            )
            text_x = abs_x + logo_w + 5
        except Exception:
            pass

    # "Digitally Signed" label
    label_fs = max(5, min(header_h * 0.42, 7.5))
    c.setFont("Helvetica-Bold", label_fs)
    c.setFillColor(white)
    c.drawString(text_x, header_y + (header_h - label_fs) * 0.38, "Digitally Signed")

    # ── Signature image area (white background) ───────────────────────────
    c.setFillColor(white)
    c.rect(abs_x, sig_y, abs_w, sig_h, stroke=0, fill=1)

    if signer.signature_image:
        try:
            with signer.signature_image.open('rb') as f:
                img_bytes = io.BytesIO(f.read())
            img_reader = ImageReader(img_bytes)
            pad = max(2, abs_w * 0.02)
            c.drawImage(
                img_reader,
                abs_x + pad, sig_y + pad,
                abs_w - 2 * pad, sig_h - 2 * pad,
                mask="auto", preserveAspectRatio=True,
            )
        except Exception:
            pass

    # ── Footer strip (light gray with annotation text) ────────────────────
    if annotations and footer_h > 0:
        c.setFillColor(surface)
        c.rect(abs_x, abs_y_pdf, abs_w, footer_h, stroke=0, fill=1)
        # Cover top corners so they don't protrude into sig area
        c.roundRect(abs_x, abs_y_pdf, abs_w, footer_h * 1.4, radius, stroke=0, fill=1)
        c.rect(abs_x, abs_y_pdf + footer_h * 0.7, abs_w, footer_h * 0.3, stroke=0, fill=1)

        # Thin separator line between sig area and footer
        c.setStrokeColor(rl_colors.HexColor("#e2e8f0"))
        c.setLineWidth(0.4)
        c.line(abs_x, abs_y_pdf + footer_h, abs_x + abs_w, abs_y_pdf + footer_h)

        ann_fs = max(5, min(footer_h / max(len(annotations), 1) * 0.72, 7))
        c.setFont("Helvetica", ann_fs)
        c.setFillColor(text_md)
        # Stack annotations in footer; last item right-aligned if it looks like a date
        y_ann = abs_y_pdf + footer_h - ann_fs - 1.5
        for i, text in enumerate(annotations):
            if i == len(annotations) - 1 and len(text) <= 20:
                c.drawRightString(abs_x + abs_w - 2, y_ann, text[:35])
            else:
                c.drawString(abs_x + 2, y_ann, text[:40])
            y_ann -= ann_fs + 1.5


# ---------------------------------------------------------------------------
# Overlay builder — dispatches to the correct stamp style
# ---------------------------------------------------------------------------

def _build_overlay(vis_w, vis_h, placements_for_page,
                   canvas_w=None, canvas_h=None,
                   origin_x=0.0, origin_y=0.0,
                   stamp_style="simple", site_name="E-Sign", logo_reader=None):
    """
    Return a BytesIO containing a single-page PDF overlay.

    vis_w/vis_h   — visible (CropBox) dimensions; placement fractions are
                    relative to this space.
    canvas_w/h    — full page (MediaBox) canvas size for the overlay PDF;
                    must match the destination page so merge_page aligns.
    origin_x/y    — CropBox lower-left corner in MediaBox coordinates
                    (0, 0 for most PDFs where CropBox == MediaBox).
    """
    from reportlab.pdfgen import canvas as rl_canvas

    cw = canvas_w if canvas_w is not None else vis_w
    ch = canvas_h if canvas_h is not None else vis_h

    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(cw, ch))

    for placement, signer in placements_for_page:
        abs_w = placement.width  * vis_w
        abs_h = placement.height * vis_h

        # Coordinates in the full MediaBox coordinate system
        abs_x = origin_x + placement.x * vis_w
        # PDF y-axis is bottom-left; stored y is fraction from top of CropBox
        abs_y_pdf = origin_y + vis_h - placement.y * vis_h - abs_h

        annotations = _build_annotations(signer)

        if stamp_style == "branded":
            _stamp_branded(c, abs_x, abs_y_pdf, abs_w, abs_h, signer, annotations,
                           site_name=site_name, logo_reader=logo_reader)
        else:
            _stamp_simple(c, abs_x, abs_y_pdf, abs_w, abs_h, signer, annotations)

    c.save()
    buf.seek(0)
    return buf


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def stamp_document(document) -> bytes | None:
    """
    Read the original PDF, stamp each signer's signature at their placement
    boxes using the document's stamp style, and return the final PDF as bytes.
    Returns None if stamping fails.
    """
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError:
        return None

    try:
        stamp_style = getattr(document, "certificate_style", "simple") or "simple"

        # Fetch branding once for all branded stamps
        site_name, logo_reader = (
            _fetch_site_branding() if stamp_style == "branded" else ("E-Sign", None)
        )

        with document.original_pdf.open('rb') as f:
            original_bytes = io.BytesIO(f.read())
        reader = PdfReader(original_bytes)
        writer = PdfWriter()

        placements = list(
            document.placements.filter(
                signer__status="signed"
            ).select_related("signer")
        )

        logger.info(
            "stamp_document doc=%s style=%s placements_found=%d signers_signed=%s",
            document.pk, stamp_style, len(placements),
            [str(p.signer_id) for p in placements],
        )

        if not placements:
            logger.warning(
                "stamp_document doc=%s: NO placements found for signed signers — "
                "check that placement boxes were configured before sending.",
                document.pk,
            )

        placements_by_page: dict[int, list] = {}
        for p in placements:
            placements_by_page.setdefault(p.page_number, []).append((p, p.signer))

        for page_index, page in enumerate(reader.pages):
            page_num = page_index + 1
            mediabox = page.mediabox
            cropbox = page.cropbox if page.cropbox else mediabox

            # Canvas uses full mediabox size so merge_page aligns exactly
            cw = float(mediabox.width)
            ch = float(mediabox.height)

            # Vis (rendering space) uses cropbox size, as PDF.js does
            vis_w = float(cropbox.width)
            vis_h = float(cropbox.height)

            # The cropbox origin might be offset relative to the mediabox origin
            origin_x = float(cropbox.left - mediabox.left)
            origin_y = float(cropbox.bottom - mediabox.bottom)

            page_placements = placements_by_page.get(page_num, [])
            if page_placements:
                overlay_buf = _build_overlay(
                    vis_w, vis_h, page_placements,
                    canvas_w=cw, canvas_h=ch,
                    origin_x=origin_x, origin_y=origin_y,
                    stamp_style=stamp_style,
                    site_name=site_name,
                    logo_reader=logo_reader,
                )
                overlay_reader = PdfReader(overlay_buf)
                page.merge_page(overlay_reader.pages[0])

            writer.add_page(page)

        # Compress streams to prevent output PDF size bloat
        for wpage in writer.pages:
            if hasattr(wpage, "compress_content_streams"):
                try:
                    wpage.compress_content_streams()
                except Exception:
                    pass

        output = io.BytesIO()
        writer.write(output)
        output.seek(0)
        return output.read()

    except Exception as exc:
        logger.exception("stamp_document failed for document %s: %s", document.pk, exc)
        return None
