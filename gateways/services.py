"""
Gateways — Evolution API Service
==================================
Client class for interacting with the Evolution API (WhatsApp gateway).

All configuration is read from Django settings — no hardcoded credentials.

Usage::

    from gateways.services import EvolutionAPIService

    svc = EvolutionAPIService()
    svc.send_whatsapp_message("+6281234567890", "Hello from RoC Desk!")
"""
from __future__ import annotations

import base64
import logging
from typing import Optional

import requests
from django.conf import settings
from django.core.files.base import ContentFile

logger = logging.getLogger(__name__)


class EvolutionAPIService:
    """
    Stateless service for the Evolution API WhatsApp gateway.

    Reads connection parameters from ``django.conf.settings``:

    - ``EVOLUTION_API_URL``          — base URL of the Evolution API instance.
    - ``EVOLUTION_API_KEY``          — API key for authentication.
    - ``EVOLUTION_INSTANCE_NAME``    — WhatsApp instance name.
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self) -> None:
        self.base_url: str = settings.EVOLUTION_API_URL.rstrip("/")
        self.api_key: str = settings.EVOLUTION_API_KEY
        self.instance: str = settings.EVOLUTION_INSTANCE_NAME
        self.timeout: int = 30  # seconds

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        """Build standard request headers."""
        return {
            "Content-Type": "application/json",
            "apikey": self.api_key,
        }

    def _build_url(self, path: str) -> str:
        """Construct a full API URL for the configured instance."""
        return f"{self.base_url}/{path}/{self.instance}"

    # ------------------------------------------------------------------
    # Public API — Send Messages
    # ------------------------------------------------------------------

    def send_whatsapp_message(
        self,
        phone_number: str,
        text: str,
    ) -> dict | None:
        """
        Send a text message via WhatsApp through Evolution API.

        Args:
            phone_number: Recipient phone in E.164 format (e.g. ``+6281234567890``).
            text: Message body.

        Returns:
            API response as dict, or None on failure.
        """
        # Strip leading '+' — Evolution API expects bare digits
        clean_number = phone_number.lstrip("+")

        url = self._build_url("message/sendText")
        payload = {
            "number": clean_number,
            "text": text,
        }

        try:
            response = requests.post(
                url,
                json=payload,
                headers=self._headers(),
                timeout=self.timeout,
            )
            response.raise_for_status()
            data: dict = response.json()
            logger.info(
                "WhatsApp message sent to %s — message_id: %s",
                clean_number,
                data.get("key", {}).get("id", "unknown"),
            )
            return data

        except requests.RequestException as exc:
            logger.error(
                "Failed to send WhatsApp message to %s: %s",
                clean_number,
                exc,
            )
            return None

    # ------------------------------------------------------------------
    # Public API — Download Media
    # ------------------------------------------------------------------

    def download_media(
        self,
        media_url: Optional[str] = None,
        base64_data: Optional[str] = None,
        mime_type: str = "application/octet-stream",
        filename: str = "attachment",
    ) -> Optional[ContentFile]:
        """
        Download or decode WhatsApp media into a Django ``ContentFile``.

        Supports two modes:
        1. **URL mode** — fetches media from a URL provided by Evolution API.
        2. **Base64 mode** — decodes inline base64 media data.

        Args:
            media_url: URL to fetch media from (Evolution API media endpoint).
            base64_data: Base64-encoded media content.
            mime_type: MIME type of the media.
            filename: Desired filename for the resulting ContentFile.

        Returns:
            A ``ContentFile`` ready for Django model assignment, or None on failure.
        """
        content_bytes: Optional[bytes] = None

        # --- Mode 1: URL download ---
        if media_url:
            try:
                response = requests.get(
                    media_url,
                    headers={"apikey": self.api_key},
                    timeout=self.timeout,
                    stream=True,
                )
                response.raise_for_status()
                content_bytes = response.content
                logger.info(
                    "Downloaded media from URL: %s (%d bytes)",
                    media_url,
                    len(content_bytes),
                )
            except requests.RequestException as exc:
                logger.error("Failed to download media from %s: %s", media_url, exc)
                return None

        # --- Mode 2: Base64 decode ---
        elif base64_data:
            try:
                content_bytes = base64.b64decode(base64_data)
                logger.info(
                    "Decoded base64 media: %s (%d bytes)",
                    filename,
                    len(content_bytes),
                )
            except Exception as exc:
                logger.error("Failed to decode base64 media: %s", exc)
                return None

        else:
            logger.warning("download_media called with no media_url or base64_data.")
            return None

        return ContentFile(content_bytes, name=filename)

    # ------------------------------------------------------------------
    # Public API — Webhook Payload Parsing
    # ------------------------------------------------------------------

    @staticmethod
    def extract_sender_phone(payload: dict) -> Optional[str]:
        """
        Extract the sender phone number from an Evolution API webhook payload.

        Evolution API sends the remote JID as ``<number>@s.whatsapp.net``.
        This method strips the suffix and prepends ``+`` for E.164.

        Args:
            payload: Raw webhook JSON payload.

        Returns:
            Phone number in E.164 format (e.g. ``+6281234567890``), or None.
        """
        try:
            data = payload.get("data", payload)
            key = data.get("key", {})
            # Evolution API may send real number in remoteJidAlt when remoteJid is a @lid
            remote_jid = key.get("remoteJidAlt") or key.get("remoteJid", "")

            if not remote_jid or "@g.us" in remote_jid:
                # Group messages — skip
                return None

            phone = remote_jid.split("@")[0]
            if phone:
                return f"+{phone}"
        except Exception as exc:
            logger.error("Error extracting sender phone: %s", exc)

        return None

    @staticmethod
    def extract_message_body(payload: dict) -> str:
        """
        Extract the text body from an Evolution API webhook payload.

        Handles text messages, extended text, and caption-based media.

        Args:
            payload: Raw webhook JSON payload.

        Returns:
            Message text, or empty string if none found.
        """
        try:
            data = payload.get("data", payload)
            message = data.get("message", {})

            # Plain text
            if "conversation" in message:
                return message["conversation"]

            # Extended text (quoted, linked)
            ext = message.get("extendedTextMessage", {})
            if ext.get("text"):
                return ext["text"]

            # Media with caption
            for media_key in (
                "imageMessage",
                "videoMessage",
                "documentMessage",
                "audioMessage",
            ):
                media = message.get(media_key, {})
                if media.get("caption"):
                    return media["caption"]

            return ""
        except Exception as exc:
            logger.error("Error extracting message body: %s", exc)
            return ""

    @staticmethod
    def extract_message_id(payload: dict) -> str:
        """Extract the Evolution API message ID for deduplication."""
        try:
            data = payload.get("data", payload)
            return data.get("key", {}).get("id", "")
        except Exception:
            return ""

    @staticmethod
    def extract_media_info(payload: dict) -> Optional[dict]:
        """
        Extract media information from the webhook payload.

        Returns:
            A dict with keys ``media_url``, ``base64``, ``mime_type``,
            ``filename`` if media is present, or None.
        """
        try:
            data = payload.get("data", payload)
            message = data.get("message", {})

            for media_key in (
                "imageMessage",
                "videoMessage",
                "documentMessage",
                "audioMessage",
                "stickerMessage",
            ):
                media = message.get(media_key)
                if media:
                    mime = media.get("mimetype", "application/octet-stream")
                    ext = mime.split("/")[-1].split(";")[0]
                    fname = media.get("fileName", f"attachment.{ext}")

                    return {
                        "media_url": media.get("url"),
                        "base64": media.get("base64"),
                        "mime_type": mime,
                        "filename": fname,
                    }
        except Exception as exc:
            logger.error("Error extracting media info: %s", exc)

        return None


# ======================================================================
# Gateways — IMAP Email Service
# ======================================================================
import email
from email.header import decode_header
import imaplib

class ImapEmailService:
    """
    Client class for fetching emails via IMAP.
    """
    def __init__(self):
        self.host = getattr(settings, "IMAP_HOST", "imap.gmail.com")
        self.user = getattr(settings, "IMAP_USER", "")
        self.password = getattr(settings, "IMAP_APP_PASSWORD", "")

    def connect(self) -> Optional[imaplib.IMAP4_SSL]:
        if not self.user or not self.password:
            logger.warning("IMAP credentials are not configured. Cannot fetch emails.")
            return None
        try:
            mail = imaplib.IMAP4_SSL(self.host)
            mail.login(self.user, self.password)
            mail.select("inbox")
            return mail
        except Exception as e:
            logger.error("Failed to connect to IMAP server: %s", e)
            return None

    def _decode_str(self, s: str) -> str:
        """Decode email header string safely."""
        if not s:
            return ""
        try:
            decoded_list = decode_header(s)
            res = ""
            for content, encoding in decoded_list:
                if isinstance(content, bytes):
                    encoding = encoding or "utf-8"
                    try:
                        res += content.decode(encoding, errors='replace')
                    except LookupError:
                        res += content.decode("utf-8", errors='replace')
                else:
                    res += str(content)
            return res
        except Exception:
            return str(s)

    def fetch_unread_emails(self):
        """
        Connects via IMAP, fetches all UNSEEN emails, yields parsed dicts,
        and marks them as READ.
        """
        mail = self.connect()
        if not mail:
            return

        try:
            # Search for unread emails since today in Inbox
            from datetime import date
            today_date = date.today().strftime("%d-%b-%Y")
            status, messages = mail.search(None, f'(UNSEEN SINCE "{today_date}")')
            if status != "OK" or not messages[0]:
                return

            email_ids = messages[0].split()
            
            # SORT REVERSE: Process NEWEST emails first.
            # LIMIT: Process max 50 emails per Celery polling cycle to prevent timeout.
            # (Background backlog will be cleared 50 emails at a time every minute).
            email_ids = email_ids[::-1][:50]
            
            for e_id in email_ids:
                res, msg_data = mail.fetch(e_id, "(RFC822)")
                if res != "OK":
                    continue

                for response_part in msg_data:
                    if isinstance(response_part, tuple):
                        msg = email.message_from_bytes(response_part[1])
                        
                        subject = self._decode_str(msg.get("Subject", ""))
                        from_ = self._decode_str(msg.get("From", ""))
                        
                        text_content = ""
                        html_content = ""
                        attachments = []

                        # Navigate through email parts
                        if msg.is_multipart():
                            for part in msg.walk():
                                content_type = part.get_content_type()
                                content_disposition = str(part.get("Content-Disposition"))

                                # Handle attachments
                                if "attachment" in content_disposition or part.get_filename():
                                    filename = part.get_filename()
                                    if filename:
                                        filename = self._decode_str(filename)
                                        content = part.get_payload(decode=True)
                                        if content:
                                            attachments.append({
                                                "filename": filename,
                                                "content": content,
                                                "mime_type": content_type
                                            })
                                # Handle text/html body
                                elif "attachment" not in content_disposition:
                                    payload = part.get_payload(decode=True)
                                    if payload:
                                        if content_type == "text/plain":
                                            text_content += payload.decode("utf-8", errors='replace')
                                        elif content_type == "text/html":
                                            html_content += payload.decode("utf-8", errors='replace')
                        else:
                            # Not multipart, just a simple text email
                            payload = msg.get_payload(decode=True)
                            if payload:
                                content_type = msg.get_content_type()
                                if content_type == "text/html":
                                    html_content = payload.decode("utf-8", errors='replace')
                                else:
                                    text_content = payload.decode("utf-8", errors='replace')

                        # Extract Message-ID for email threading
                        message_id_header = msg.get("Message-ID", "")

                        yield {
                            "from": from_,
                            "subject": subject,
                            "text": text_content,
                            "html": html_content,
                            "attachments": attachments,
                            "message_id": message_id_header,
                        }
                        
                # Mark as read
                mail.store(e_id, '+FLAGS', '\\Seen')

        except Exception as e:
            logger.error("Error fetching IMAP emails: %s", e)
        finally:
            try:
                mail.close()
                mail.logout()
            except Exception:
                pass
