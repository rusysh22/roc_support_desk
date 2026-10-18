FONT = (
    "'SF Pro Text','SF Pro Display',-apple-system,BlinkMacSystemFont,"
    "'Helvetica Neue',Helvetica,Arial,sans-serif"
)
DISPLAY_FONT = (
    "'SF Pro Display','SF Pro Text',-apple-system,BlinkMacSystemFont,"
    "'Helvetica Neue',Helvetica,Arial,sans-serif"
)
LOGO_CID = "site-logo"
CTA_BG = "#4f46e5"
CTA_FG = "#ffffff"
ACCENT = "#4f46e5"
EYEBROW = "#6366f1"


def _logo_field():
    """The SiteConfig logo meant for dark backgrounds (the email footer is dark)."""
    try:
        from core.models import SiteConfig

        return SiteConfig.get_solo().logo_for_dark_bg
    except Exception:
        return None


def get_logo_bytes() -> bytes:
    logo = _logo_field()
    if not logo:
        return b""
    try:
        with logo.open("rb") as fh:
            return fh.read()
    except Exception:
        return b""


def _logo_subtype(name: str) -> str:
    ext = (name or "").rsplit(".", 1)[-1].lower()
    return {"jpg": "jpeg", "jpeg": "jpeg", "gif": "gif", "webp": "webp"}.get(ext, "png")


def attach_logo(email_msg) -> bool:
    from email.mime.image import MIMEImage

    data = get_logo_bytes()
    if not data:
        return False
    logo = _logo_field()
    subtype = _logo_subtype(getattr(logo, "name", ""))
    email_msg.mixed_subtype = "related"
    image = MIMEImage(data, _subtype=subtype)
    image.add_header("Content-ID", f"<{LOGO_CID}>")
    image.add_header("Content-Disposition", "inline", filename=f"logo.{'jpg' if subtype == 'jpeg' else subtype}")
    email_msg.attach(image)
    return True


def render_branded_email(
    *,
    site_name: str,
    title: str,
    body_html: str,
    eyebrow: str = "",
    cta_url: str = "",
    cta_label: str = "Open ticket",
    footer_note: str = "",
) -> str:
    from django.template.loader import render_to_string

    return render_to_string(
        "emails/_base.html",
        {
            "site_name": site_name,
            "eyebrow": eyebrow or f"{site_name} · Support Desk",
            "title": title,
            "body_html": body_html,
            "cta_url": cta_url,
            "cta_label": cta_label,
            "footer_note": footer_note
            or "Automated desk notification. Do not reply to this email.",
            "logo_cid": LOGO_CID if get_logo_bytes() else "",
            "font": FONT,
            "display_font": DISPLAY_FONT,
            "cta_bg": CTA_BG,
            "cta_fg": CTA_FG,
            "accent": ACCENT,
            "eyebrow_color": EYEBROW,
        },
    )


def render_branded_from_template(
    body_template: str,
    context: dict,
    *,
    site_name: str,
    title: str,
    eyebrow: str = "",
    cta_url: str = "",
    cta_label: str = "Open ticket",
    footer_note: str = "",
) -> str:
    from django.template.loader import render_to_string

    body_html = render_to_string(body_template, context)
    return render_branded_email(
        site_name=site_name,
        title=title,
        body_html=body_html,
        eyebrow=eyebrow,
        cta_url=cta_url,
        cta_label=cta_label,
        footer_note=footer_note,
    )
