"""
Redirect-portal media helpers: upload validation + compression.

Images are compressed synchronously (Pillow, fast). Videos are stored as
uploaded and then re-encoded in the background by
``links.tasks.compress_portal_video`` because ffmpeg can take a while.
"""
import io
import logging
import os
import re
import shutil
import subprocess
import tempfile
from urllib.parse import parse_qs, urlparse

from django.core.files.base import ContentFile
from PIL import Image, ImageOps

from core.upload_security import ALLOWED_IMAGE_MIME_TYPES, detect_mime, safe_filename

logger = logging.getLogger(__name__)

IMAGE_MAX_MB = 10
VIDEO_MAX_MB = 45  # ingress proxy-body-size is 50m
ALLOWED_VIDEO_MIME_TYPES = {"video/mp4", "video/webm", "video/quicktime"}

IMAGE_MAX_SIDE = 1600
IMAGE_QUALITY = 80

VIDEO_MAX_WIDTH = 1280
VIDEO_CRF = "28"
VIDEO_TIMEOUT_SECONDS = 540


def classify_upload(uploaded_file):
    """Return ``(kind, mime, error)`` — kind is 'image', 'video' or None."""
    mime = detect_mime(uploaded_file)
    size_mb = (uploaded_file.size or 0) / (1024 * 1024)
    if mime in ALLOWED_IMAGE_MIME_TYPES:
        if size_mb > IMAGE_MAX_MB:
            return None, mime, f"Images must be {IMAGE_MAX_MB} MB or smaller."
        return "image", mime, None
    if mime in ALLOWED_VIDEO_MIME_TYPES:
        if size_mb > VIDEO_MAX_MB:
            return None, mime, f"Videos must be {VIDEO_MAX_MB} MB or smaller."
        return "video", mime, None
    return None, mime, "Only images (JPG, PNG, WebP, GIF) or videos (MP4, WebM, MOV) are allowed."


def compress_image(uploaded_file, mime):
    """
    Downscale to ``IMAGE_MAX_SIDE`` and re-encode as WebP. Animated GIFs are
    kept as-is (re-encoding would drop the animation). Falls back to the
    original file if Pillow can't process it.
    """
    try:
        uploaded_file.seek(0)
        img = Image.open(uploaded_file)
        if getattr(img, "is_animated", False):
            uploaded_file.seek(0)
            return ContentFile(uploaded_file.read(), name=safe_filename(uploaded_file.name, mime))
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA" if "A" in img.getbands() else "RGB")
        img.thumbnail((IMAGE_MAX_SIDE, IMAGE_MAX_SIDE), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="WEBP", quality=IMAGE_QUALITY, method=6)
        return ContentFile(buf.getvalue(), name=safe_filename("portal.webp", "image/webp"))
    except Exception:
        logger.exception("Portal image compression failed; storing original")
        uploaded_file.seek(0)
        return ContentFile(uploaded_file.read(), name=safe_filename(uploaded_file.name, mime))


def transcode_video(src_path, dst_path):
    """
    Re-encode to H.264/AAC MP4 (max 1280px wide, CRF 28, faststart so it
    streams before fully downloaded). Returns True on success.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        logger.warning("ffmpeg not found — portal video left uncompressed")
        return False
    cmd = [
        ffmpeg, "-y", "-loglevel", "error", "-i", src_path,
        "-vf", f"scale='trunc(min({VIDEO_MAX_WIDTH},iw)/2)*2':-2",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", VIDEO_CRF, "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "96k",
        "-movflags", "+faststart",
        dst_path,
    ]
    try:
        subprocess.run(cmd, check=True, timeout=VIDEO_TIMEOUT_SECONDS, capture_output=True)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        logger.warning("Portal video transcode failed: %s", getattr(exc, "stderr", exc))
        return False
    return os.path.exists(dst_path) and os.path.getsize(dst_path) > 0


def has_audio_track(path):
    """True / False when ffprobe can tell whether the video file has an audio stream, None when it cannot."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", path],
            check=True, timeout=30, capture_output=True, text=True,
        ).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None
    return bool(out.strip())


def uploaded_video_has_audio(uploaded_file):
    """has_audio_track() for an uploaded (in-memory or temporary) file."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "probe" + (os.path.splitext(uploaded_file.name)[1] or ".mp4"))
        uploaded_file.seek(0)
        with open(path, "wb") as out:
            for chunk in iter(lambda: uploaded_file.read(1024 * 1024), b""):
                out.write(chunk)
        uploaded_file.seek(0)
        return has_audio_track(path)


def compress_stored_video(field_file):
    """
    Download a stored video (works with local and Azure storage), transcode
    it, and return a ``ContentFile`` of the result — or None when the
    transcode failed or didn't make the file smaller.
    """
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "src" + (os.path.splitext(field_file.name)[1] or ".mp4"))
        dst = os.path.join(tmp, "out.mp4")
        with field_file.open("rb") as fh, open(src, "wb") as out:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                out.write(chunk)
        if not transcode_video(src, dst):
            return None
        if os.path.getsize(dst) >= os.path.getsize(src):
            return None
        with open(dst, "rb") as fh:
            return ContentFile(fh.read(), name=safe_filename("portal.mp4", "video/mp4"))


# ---------------------------------------------------------------------------
# Video links (YouTube / TikTok) for the redirect portal
# ---------------------------------------------------------------------------
# Only these two sites are ever put in an <iframe>, and the iframe address is rebuilt from the
# video id — the visitor-facing page never embeds a URL exactly as it was typed.

YOUTUBE_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
    "youtube-nocookie.com", "www.youtube-nocookie.com",
}
TIKTOK_HOSTS = {"tiktok.com", "www.tiktok.com", "m.tiktok.com"}
TIKTOK_SHORT_HOSTS = {"vm.tiktok.com", "vt.tiktok.com"}

YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
TIKTOK_VIDEO_PATH = re.compile(r"^/@(?P<user>[\w.\-]+)/video/(?P<id>\d{8,25})/?$")
TIKTOK_EMBED_PATH = re.compile(r"^/embed(?:/v2)?/(?P<id>\d{8,25})/?$")
TIKTOK_SHORT_PATH = re.compile(r"^/(?:t/)?[A-Za-z0-9]{5,20}/?$")
MAX_RESOLVE_HOPS = 4
RESOLVE_TIMEOUT = 5


class VideoLinkError(ValueError):
    pass


def _youtube_id(parsed):
    host, path = parsed.netloc.lower().split(":")[0], parsed.path
    if host == "youtu.be":
        candidate, portrait = path.strip("/").split("/")[0], False
    elif host in YOUTUBE_HOSTS:
        parts = [part for part in path.split("/") if part]
        if path.rstrip("/") == "/watch":
            candidate, portrait = (parse_qs(parsed.query).get("v") or [""])[0], False
        elif len(parts) >= 2 and parts[0] in ("shorts", "embed", "live", "v"):
            candidate, portrait = parts[1], parts[0] == "shorts"
        else:
            return None
    else:
        return None
    if not YOUTUBE_ID.match(candidate or ""):
        raise VideoLinkError("That YouTube link has no valid video id.")
    return candidate, portrait


def parse_video_link(url):
    """Return ``{"provider", "video_id", "canonical", "portrait"}`` for a YouTube or TikTok video
    link, ``{"provider": "tiktok-short", ...}`` for a TikTok share link that still has to be
    resolved, or raise ``VideoLinkError``."""
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise VideoLinkError("Enter the full video link, starting with https://")
    host = parsed.netloc.lower().split(":")[0]

    youtube = _youtube_id(parsed)
    if youtube:
        video_id, portrait = youtube
        canonical = (f"https://www.youtube.com/shorts/{video_id}" if portrait
                     else f"https://www.youtube.com/watch?v={video_id}")
        return {"provider": "youtube", "video_id": video_id, "canonical": canonical, "portrait": portrait}

    if host in TIKTOK_HOSTS:
        match = TIKTOK_VIDEO_PATH.match(parsed.path)
        if match:
            return {"provider": "tiktok", "video_id": match["id"], "portrait": True,
                    "canonical": f"https://www.tiktok.com/@{match['user']}/video/{match['id']}"}
        match = TIKTOK_EMBED_PATH.match(parsed.path)
        if match:
            return {"provider": "tiktok", "video_id": match["id"], "portrait": True,
                    "canonical": f"https://www.tiktok.com/embed/v2/{match['id']}"}
        if parsed.path.startswith("/t/") and TIKTOK_SHORT_PATH.match(parsed.path):
            return {"provider": "tiktok-short", "video_id": "", "canonical": url, "portrait": True}
    if host in TIKTOK_SHORT_HOSTS and TIKTOK_SHORT_PATH.match(parsed.path):
        return {"provider": "tiktok-short", "video_id": "", "canonical": url, "portrait": True}

    raise VideoLinkError("Only YouTube and TikTok video links are supported.")


def resolve_tiktok_short(url, opener=None):
    """Follow a TikTok share link (vm.tiktok.com/…, tiktok.com/t/…) to the full video link.

    Redirects are followed by hand, at most ``MAX_RESOLVE_HOPS``, and every hop must stay on a
    TikTok host, so a typed address can never make the server request somewhere else."""
    import requests

    get = opener or requests.get
    current = url
    for _ in range(MAX_RESOLVE_HOPS):
        host = urlparse(current).netloc.lower().split(":")[0]
        if host not in TIKTOK_HOSTS | TIKTOK_SHORT_HOSTS:
            break
        try:
            response = get(current, allow_redirects=False, timeout=RESOLVE_TIMEOUT,
                           headers={"User-Agent": "Mozilla/5.0 (compatible; RocDeskLinkResolver)"})
        except Exception as exc:
            raise VideoLinkError("Could not reach TikTok to read that share link. Paste the full video link instead.") from exc
        location = response.headers.get("Location", "")
        if not (300 <= response.status_code < 400 and location):
            break
        current = requests.compat.urljoin(current, location)
        info = None
        try:
            info = parse_video_link(current)
        except VideoLinkError:
            pass
        if info and info["provider"] == "tiktok":
            return info
    raise VideoLinkError("Could not find the video behind that TikTok share link. Paste the full video link instead.")


def normalize_video_link(url, resolver=resolve_tiktok_short):
    """The link as it is stored: a canonical YouTube / TikTok video address."""
    info = parse_video_link(url)
    if info["provider"] == "tiktok-short":
        info = resolver(info["canonical"])
    return info


def embed_for(url, sound=True):
    """What the portal page needs to embed a stored link, or None when it isn't a supported one."""
    try:
        info = parse_video_link(url)
    except VideoLinkError:
        return None
    if info["provider"] == "youtube":
        params = "autoplay=1&playsinline=1&rel=0&modestbranding=1" + ("" if sound else "&mute=1")
        src = f"https://www.youtube-nocookie.com/embed/{info['video_id']}?{params}"
    elif info["provider"] == "tiktok":
        src = f"https://www.tiktok.com/embed/v2/{info['video_id']}"
    else:
        return None
    return {"provider": info["provider"], "src": src, "portrait": info["portrait"]}
