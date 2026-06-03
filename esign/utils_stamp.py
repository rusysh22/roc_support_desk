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

def _stamp_branded(c, abs_x, abs_y_pdf, abs_w, abs_h, signer, annotations, logo_reader, site_name=""):
    import io
    from reportlab.lib.utils import ImageReader
    from reportlab.lib import colors as rl_colors

    text_color = rl_colors.HexColor("#1e293b")
    pad = min(abs_w, abs_h) * 0.05

    # ── Top-left label ────────────────────────────────────────────────────────
    label_fs = max(5, min(abs_h * 0.1, 8))
    c.setFont("Helvetica", label_fs)
    c.setFillColor(text_color)
    c.drawString(abs_x + pad, abs_y_pdf + abs_h - pad - label_fs, "Digital Signed")

    # ── Bottom-right logo ─────────────────────────────────────────────────────
    if logo_reader:
        try:
            img_w, img_h = logo_reader.getSize()
            aspect = img_w / float(img_h)
            max_logo_w = abs_w * 0.4
            max_logo_h = abs_h * 0.3
            
            draw_h = min(max_logo_h, max_logo_w / aspect)
            draw_w = draw_h * aspect
            
            c.drawImage(
                logo_reader,
                abs_x + abs_w - draw_w - pad, abs_y_pdf + pad,
                width=draw_w, height=draw_h,
                mask="auto", preserveAspectRatio=True
            )
        except Exception:
            pass

    # ── Bottom-left annotations ───────────────────────────────────────────────
    if annotations:
        ann_fs = max(4.5, min(abs_h * 0.09, 7.5))
        c.setFillColor(text_color)
        y_ann = abs_y_pdf + pad + (len(annotations) - 1) * (ann_fs + 3)
        for i, text in enumerate(annotations):
            if i == 0:
                c.setFont("Helvetica-Bold", ann_fs + 0.5)
            else:
                c.setFont("Helvetica", ann_fs)
            c.drawString(abs_x + pad, y_ann, text)
            y_ann -= (ann_fs + 3)

    # ── Signature in the center ───────────────────────────────────────────────
    if signer.signature_image:
        try:
            with signer.signature_image.open('rb') as f:
                img_bytes = io.BytesIO(f.read())
            sig_reader = ImageReader(img_bytes)
            
            sig_y = abs_y_pdf + abs_h * 0.25
            sig_h = abs_h * 0.6
            
            c.drawImage(
                sig_reader,
                abs_x + pad, sig_y,
                abs_w - 2 * pad, sig_h,
                mask="auto", preserveAspectRatio=True
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Overlay builder — dispatches to the correct stamp style
# ---------------------------------------------------------------------------

def _build_overlay(vis_w, vis_h, placements_for_page,
                   canvas_w=None, canvas_h=None,
                   cropbox_left=0.0, cropbox_bottom=0.0,
                   rotation=0,
                   stamp_style="simple", site_name="E-Sign", logo_reader=None):
    """
    Return a BytesIO containing a single-page PDF overlay.
    """
    from reportlab.pdfgen import canvas as rl_canvas

    cw = canvas_w if canvas_w is not None else vis_w
    ch = canvas_h if canvas_h is not None else vis_h

    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(cw, ch))

    # Apply transformation to match PDF.js visual coordinate space exactly
    c.saveState()
    
    # 1. Move origin to cropbox bottom-left
    c.translate(cropbox_left, cropbox_bottom)
    
    # 2. Apply rotation to match the visual rendering
    if rotation == 90:
        c.translate(vis_h, 0)
        c.rotate(90)
    elif rotation == 180:
        c.translate(vis_w, vis_h)
        c.rotate(180)
    elif rotation == 270:
        c.translate(0, vis_w)
        c.rotate(270)

    for placement, signer in placements_for_page:
        abs_w = placement.width  * vis_w
        abs_h = placement.height * vis_h

        # In this transformed space, X goes right, Y goes up.
        # placement.y is from the top, so we invert it for Y.
        abs_x = placement.x * vis_w
        abs_y_pdf = vis_h - (placement.y * vis_h) - abs_h

        annotations = _build_annotations(signer)

        if stamp_style == "branded":
            _stamp_branded(c, abs_x, abs_y_pdf, abs_w, abs_h, signer, annotations,
                           site_name=site_name, logo_reader=logo_reader)
        else:
            _stamp_simple(c, abs_x, abs_y_pdf, abs_w, abs_h, signer, annotations)

    c.restoreState()
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

            try:
                rotation = int(getattr(page, "rotation", page.get('/Rotate', 0))) % 360
            except Exception:
                rotation = 0

            # Vis (rendering space) dimensions depend on rotation
            if rotation in (90, 270):
                vis_w = float(cropbox.height)
                vis_h = float(cropbox.width)
            else:
                vis_w = float(cropbox.width)
                vis_h = float(cropbox.height)

            # We use max bounds to ensure canvas encompasses everything
            cw = max(float(mediabox.right), float(mediabox.width))
            ch = max(float(mediabox.top), float(mediabox.height))

            page_placements = placements_by_page.get(page_num, [])
            if page_placements:
                overlay_buf = _build_overlay(
                    vis_w, vis_h, page_placements,
                    canvas_w=cw, canvas_h=ch,
                    cropbox_left=float(cropbox.left),
                    cropbox_bottom=float(cropbox.bottom),
                    rotation=rotation,
                    stamp_style=stamp_style,
                    site_name=site_name,
                    logo_reader=logo_reader,
                )
                overlay_reader = PdfReader(overlay_buf)
                overlay_page = overlay_reader.pages[0]
                
                # Match mediaboxes before merging so they align perfectly
                overlay_page.mediabox = page.mediabox
                page.merge_page(overlay_page)

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
