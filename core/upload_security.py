"""
Shared helpers for validating and safely storing user-uploaded files.

Several upload surfaces (ticket portal attachments, the live-chat widget)
already validate size + real file content via ``python-magic`` and rename
uploads to a random, safe filename. This module factors that same pattern
out so newer/previously-unprotected upload points (public eForm
attachments, WhatsApp/email ingestion, project updates, app portal icons)
get the same protection instead of trusting client-supplied filenames/
MIME types.
"""
import mimetypes
import os
import uuid

import magic

# Kept in sync with the whitelist used by cases.forms.CaseCreateForm and
# cases.views_chat — the common set of content types accepted for
# document/attachment-style uploads across the app.
ALLOWED_DOCUMENT_MIME_TYPES = {
    "application/pdf",
    "image/png", "image/jpeg", "image/gif", "image/webp",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/x-ole-storage",  # .xls legacy Excel 97-2003 (detected by magic bytes)
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "text/plain", "text/csv",
    "application/zip",
}

# Narrower whitelist for image-only upload points (icons, logos, avatars).
ALLOWED_IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}

# Extensions we never trust even if the client claims/names it that way —
# forces a MIME-based extension instead so an uploaded file can't be
# served back by the webserver as executable/renderable markup.
DANGEROUS_EXTENSIONS = {
    ".php", ".php3", ".php4", ".php5", ".phtml", ".html", ".htm",
    ".exe", ".sh", ".bash", ".pl", ".py", ".js", ".mjs", ".svg", ".xml",
}


def detect_mime(content, fallback=""):
    """
    Sniff the real MIME type from file content (never trust the
    client-supplied Content-Type or the filename's extension).

    ``content`` may be raw ``bytes`` or a file-like object supporting
    ``read()``/``seek()`` (e.g. a Django ``UploadedFile``/``ContentFile``).
    """
    try:
        if isinstance(content, (bytes, bytearray)):
            return magic.from_buffer(bytes(content[:2048]), mime=True)
        pos = content.tell()
        header = content.read(2048)
        content.seek(pos)
        return magic.from_buffer(header, mime=True)
    except Exception:
        return fallback


def safe_filename(original_name, mime_type=None):
    """
    Return a random UUID-based filename, keeping the original extension
    only if it isn't in ``DANGEROUS_EXTENSIONS``; otherwise derive a safe
    extension from the detected MIME type.
    """
    ext = os.path.splitext(original_name or "")[1].lower()
    if ext in DANGEROUS_EXTENSIONS or not ext:
        ext = mimetypes.guess_extension(mime_type or "") or ".bin"
    return f"{uuid.uuid4().hex}{ext}"


def validate_upload(uploaded_file, max_size_mb, allowed_mime_types=None):
    """
    Validate the size and real content type of a Django uploaded file
    (``UploadedFile``/``ContentFile``, anything with ``.size``/``.read``/
    ``.seek``).

    Returns ``(is_valid, error_message, detected_mime)``.
    """
    allowed = ALLOWED_DOCUMENT_MIME_TYPES if allowed_mime_types is None else allowed_mime_types
    max_bytes = max_size_mb * 1024 * 1024

    size = getattr(uploaded_file, "size", None)
    if size is None:
        try:
            pos = uploaded_file.tell()
            uploaded_file.seek(0, os.SEEK_END)
            size = uploaded_file.tell()
            uploaded_file.seek(pos)
        except Exception:
            size = 0
    if size > max_bytes:
        return False, f"File exceeds the {max_size_mb} MB limit.", None

    mime = detect_mime(uploaded_file)
    if not mime:
        return False, "File type could not be verified.", None
    if mime not in allowed:
        return False, f"File type '{mime}' is not allowed.", mime
    return True, None, mime


def validate_bytes(content: bytes, max_size_mb, allowed_mime_types=None):
    """Same as ``validate_upload`` but for raw bytes (e.g. downloaded media, email attachments)."""
    allowed = ALLOWED_DOCUMENT_MIME_TYPES if allowed_mime_types is None else allowed_mime_types
    max_bytes = max_size_mb * 1024 * 1024

    if len(content) > max_bytes:
        return False, f"File exceeds the {max_size_mb} MB limit.", None

    mime = detect_mime(content)
    if not mime:
        return False, "File type could not be verified.", None
    if mime not in allowed:
        return False, f"File type '{mime}' is not allowed.", mime
    return True, None, mime
