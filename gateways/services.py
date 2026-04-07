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
    # Public API — Instance State & QR Code
    # ------------------------------------------------------------------    

    def get_instance_state(self) -> dict | None:
        """Fetch the connection status of the WhatsApp instance."""
        url = self._build_url("instance/connectionState")
        try:
            response = requests.get(url, headers=self._headers(), timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            logger.error("Failed to fetch instance state: %s", exc)
            return None

    def get_instance_info(self) -> dict | None:
        """Fetch full instance information including timestamps."""
        url = f"{self.base_url}/instance/fetchInstances"
        # Optional: ?instanceName=helpdesk-wa to filter
        params = {"instanceName": self.instance}
        try:
            response = requests.get(url, params=params, headers=self._headers(), timeout=self.timeout)
            response.raise_for_status()
            instances = response.json()
            # If it's a list, find ours
            if isinstance(instances, list):
                for inst in instances:
                    if inst.get("name") == self.instance:
                        return inst
                return instances[0] if instances else None
            return instances
        except requests.RequestException as exc:
            logger.error("Failed to fetch instance info: %s", exc)
            return None

    def find_latest_chat(self, remote_jid: str) -> dict | None:
        """
        Fetch the exact chat history data from the Evolution Database
        using the unresolved LID remote_jid.
        
        Args:
            remote_jid: The Linked Device ID (e.g., "217188...482@lid")
            
        Returns:
            A dictionary containing the chat metadata (which includes the real WhatsApp number)
            or None if the query fails/returns empty.
        """
        url = f"{self.base_url}/chat/find/{self.instance}"
        payload = {
            "where": {
                "id": remote_jid
            }
        }
        
        try:
            response = requests.post(url, json=payload, headers=self._headers(), timeout=self.timeout)
            response.raise_for_status()
            
            data = response.json()
            if data and isinstance(data, list) and len(data) > 0:
                # Evolution returns an array of matched chat records
                return data[0]
            
            # Additional fallback: Evolution API might nest the result
            if isinstance(data, dict) and "records" in data and data["records"]:
                return data["records"][0]
                
            return None
            
        except requests.RequestException as exc:
            logger.error(
                "Failed to find fallback chat using LID %s: %s",
                remote_jid,
                exc,
            )
            return None

    def find_message_by_id(self, remote_jid: str, message_id: str) -> dict | None:
        """
        Fetch a specific message from Evolution API to get full context
        (including contextInfo for quoted replies).

        Uses the ``chat/findMessages`` endpoint.

        Args:
            remote_jid: The chat JID (e.g., "6281234567890@s.whatsapp.net")
            message_id: The WhatsApp message ID (stanza ID)

        Returns:
            The message dict with full contextInfo, or None.
        """
        url = self._build_url("chat/findMessages")
        payload = {
            "where": {
                "key": {
                    "remoteJid": remote_jid,
                    "id": message_id,
                }
            },
            "limit": 1,
        }

        try:
            response = requests.post(
                url, json=payload, headers=self._headers(), timeout=self.timeout
            )
            response.raise_for_status()
            data = response.json()

            # Evolution API returns array of messages
            messages = data if isinstance(data, list) else data.get("messages", data.get("records", []))
            if messages and isinstance(messages, list) and len(messages) > 0:
                msg = messages[0]
                logger.info(
                    "findMessages returned message %s, keys=%s",
                    message_id,
                    list(msg.get("message", {}).keys()) if msg.get("message") else "no-message",
                )
                return msg
            return None
        except requests.RequestException as exc:
            logger.warning("findMessages failed for %s/%s: %s", remote_jid, message_id, exc)
            return None

    def get_qr_code(self) -> dict | None:
        """Fetch the Base64 QR code for pairing the WhatsApp instance."""
        url = self._build_url("instance/connect")
        try:
            response = requests.get(url, headers=self._headers(), timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            logger.error("Failed to fetch QR code: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Public API — Send Messages & Presence
    # ------------------------------------------------------------------

    def send_presence(self, phone_number: str, presence: str = "composing", delay: int = 5000) -> dict | None:
        """
        Send a presence update (e.g., 'composing', 'recording') to simulate a human typing.
        
        Args:
            phone_number: Recipient phone in E.164 format.
            presence: The presence state ('composing', 'recording', 'available', 'unavailable').
            delay: Duration in milliseconds for how long the presence is shown.
        """
        clean_number = phone_number.lstrip("+")
        url = self._build_url("chat/sendPresence")
        payload = {
            "number": clean_number,
            "presence": presence,
            "delay": delay
        }
        
        try:
            response = requests.post(url, json=payload, headers=self._headers(), timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            logger.error("Failed to send presence '%s' to %s: %s", presence, clean_number, exc)
            return None

    def send_whatsapp_message(
        self,
        phone_number: str,
        text: str,
        quoted_msg_id: Optional[str] = None,
    ) -> dict | None:
        """
        Send a text message via WhatsApp through Evolution API.

        Args:
            phone_number: Recipient phone in E.164 format (e.g. ``+6281234567890``).
            text: Message body.
            quoted_msg_id: External ID of the message to quote/reply to.

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
        if quoted_msg_id:
            payload["quoted"] = {"key": {"id": quoted_msg_id}}

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

    def send_whatsapp_media(
        self,
        phone_number: str,
        base64_data: str,
        mime_type: str,
        filename: str,
        caption: str = "",
    ) -> dict | None:
        """
        Send a media message (document or image) via WhatsApp through Evolution API.

        Args:
            phone_number: Recipient phone in E.164 format.
            base64_data: The base64-encoded string of the file to send.
            mime_type: The MIME type of the file.
            filename: The file name.
            caption: Optional caption to send with the media.

        Returns:
            API response as dict, or None on failure.
        """
        clean_number = phone_number.lstrip("+")
        url = self._build_url("message/sendMedia")
        
        # Determine media type for Evolution API
        # Options are: image, document, video, audio
        media_type = "document"
        if mime_type.startswith("image/"):
            media_type = "image"
        elif mime_type.startswith("video/"):
            media_type = "video"
        elif mime_type.startswith("audio/"):
            media_type = "audio"

        payload = {
            "number": clean_number,
            "mediatype": media_type,
            "mimetype": mime_type,
            "caption": caption,
            "media": base64_data,
            "fileName": filename,
        }

        try:
            response = requests.post(
                url,
                json=payload,
                headers=self._headers(),
                timeout=self.timeout + 30,  # Media uploads need more time
            )
            response.raise_for_status()
            data: dict = response.json()
            logger.info(
                "WhatsApp media sent to %s — message_id: %s",
                clean_number,
                data.get("key", {}).get("id", "unknown"),
            )
            return data

        except requests.RequestException as exc:
            logger.error(
                "Failed to send WhatsApp media to %s: %s",
                clean_number,
                exc,
            )
            return None

    def send_whatsapp_audio(
        self,
        phone_number: str,
        base64_data: str,
        mime_type: str = "audio/ogg; codecs=opus",
    ) -> dict | None:
        """
        Send a PTT (Push-to-Talk) voice note via WhatsApp.
        Uses /message/sendWhatsAppAudio/{instance} endpoint which renders
        as a voice note bubble (green with waveform) instead of a file attachment.

        Args:
            phone_number: Recipient phone in E.164 format.
            base64_data: The base64-encoded audio data.
            mime_type: Audio MIME type.

        Returns:
            API response dict, or None on failure.
        """
        clean_number = phone_number.lstrip("+")
        url = self._build_url("message/sendWhatsAppAudio")
        payload = {
            "number": clean_number,
            "audio": f"data:{mime_type};base64,{base64_data}",
        }

        try:
            response = requests.post(
                url,
                json=payload,
                headers=self._headers(),
                timeout=self.timeout + 30,
            )
            response.raise_for_status()
            data: dict = response.json()
            logger.info(
                "WhatsApp voice note sent to %s — message_id: %s",
                clean_number,
                data.get("key", {}).get("id", "unknown"),
            )
            return data

        except requests.RequestException as exc:
            logger.error(
                "Failed to send WhatsApp voice note to %s: %s",
                clean_number,
                exc,
            )
            return None

    # ------------------------------------------------------------------
    # Public API — Download Media
    # ------------------------------------------------------------------

    def get_base64_from_message(self, message_id: str) -> Optional[str]:
        """
        Fetch the decrypted base64 media payload directly from Evolution API
        using the message's unique `id`. This is required because raw URLs
        from WhatsApp webhooks are encrypted.
        """
        url = self._build_url("chat/getBase64FromMediaMessage")
        
        payload = {
            "message": {
                "key": {
                    "id": message_id
                }
            }
        }
        
        try:
            response = requests.post(
                url,
                json=payload,
                headers=self._headers(),
                timeout=self.timeout + 30,  # Decoding might take time
            )
            response.raise_for_status()
            data: dict = response.json()
            
            # Evolution API returns the base64 string directly in the 'base64' key
            return data.get("base64")
            
        except requests.RequestException as exc:
            logger.error("Failed to fetch base64 from message %s: %s", message_id, exc)
            return None

    def download_media(
        self,
        message_id: str,
        mime_type: str = "application/octet-stream",
        filename: str = "attachment",
    ) -> Optional[ContentFile]:
        """
        Download or decode WhatsApp media into a Django ``ContentFile``
        by fetching the decrypted Base64 string from Evolution API.

        Args:
            message_id: The ID of the WhatsApp message to fetch media for.
            mime_type: MIME type of the media.
            filename: Desired filename for the resulting ContentFile.

        Returns:
            A ``ContentFile`` ready for Django model assignment, or None on failure.
        """
        content_bytes: Optional[bytes] = None

        logger.info("Fetching base64 media for message_id: %s", message_id)
        base64_data = self.get_base64_from_message(message_id)

        if base64_data:
            try:
                # Some APIs prepend "data:image/jpeg;base64,". Strip it if present.
                if ";base64," in base64_data:
                    base64_data = base64_data.split(";base64,")[1]
                    
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
            logger.warning("download_media: get_base64_from_message returned None for %s", message_id)
            return None

        return ContentFile(content_bytes, name=filename)

    # ------------------------------------------------------------------
    # Public API — Read Receipts
    # ------------------------------------------------------------------

    def mark_messages_as_read(
        self,
        phone_number: str,
        message_ids: list[str],
    ) -> dict | None:
        """
        Send read receipts (blue checkmarks) for the given message IDs
        via Evolution API.

        Args:
            phone_number: Sender's phone in E.164 format.
            message_ids: List of Evolution API message IDs to mark as read.

        Returns:
            API response dict, or None on failure.
        """
        if not message_ids:
            return None

        clean_number = phone_number.lstrip("+")
        url = self._build_url("chat/markMessageAsRead")
        payload = {
            "readMessages": [
                {
                    "remoteJid": f"{clean_number}@s.whatsapp.net",
                    "fromMe": False,
                    "id": mid,
                }
                for mid in message_ids
            ]
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
                "Marked %d message(s) as read for %s",
                len(message_ids),
                clean_number,
            )
            return data
        except requests.RequestException as exc:
            logger.error(
                "Failed to mark messages as read for %s: %s",
                clean_number,
                exc,
            )
            return None

    # ------------------------------------------------------------------
    # Public API — Message Actions (Delete, Edit, React)
    # ------------------------------------------------------------------

    def delete_message_for_everyone(
        self,
        phone_number: str,
        message_id: str,
    ) -> dict | None:
        """
        Delete (revoke) a message for everyone via Evolution API.

        Args:
            phone_number: Recipient phone in E.164 format.
            message_id: Evolution API message ID to delete.

        Returns:
            API response dict, or None on failure.
        """
        clean_number = phone_number.lstrip("+")
        url = self._build_url("chat/deleteMessageForEveryone")
        payload = {
            "id": message_id,
            "remoteJid": f"{clean_number}@s.whatsapp.net",
            "fromMe": True,
        }

        try:
            response = requests.delete(
                url,
                json=payload,
                headers=self._headers(),
                timeout=self.timeout,
            )
            response.raise_for_status()
            data: dict = response.json()
            logger.info("Deleted message %s for %s", message_id, clean_number)
            return data
        except requests.RequestException as exc:
            logger.error("Failed to delete message %s: %s", message_id, exc)
            return None

    def update_message(
        self,
        phone_number: str,
        message_id: str,
        new_text: str,
    ) -> dict | None:
        """
        Edit/update a sent message text via Evolution API.

        Args:
            phone_number: Recipient phone in E.164 format.
            message_id: Evolution API message ID to edit.
            new_text: New message text.

        Returns:
            API response dict, or None on failure.
        """
        clean_number = phone_number.lstrip("+")
        url = self._build_url("chat/updateMessage")
        payload = {
            "number": clean_number,
            "text": new_text,
            "key": {
                "remoteJid": f"{clean_number}@s.whatsapp.net",
                "fromMe": True,
                "id": message_id,
            },
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
            logger.info("Updated message %s for %s", message_id, clean_number)
            return data
        except requests.RequestException as exc:
            logger.error("Failed to update message %s: %s", message_id, exc)
            return None

    def send_reaction(
        self,
        phone_number: str,
        message_id: str,
        emoji: str,
        from_me: bool = False,
    ) -> dict | None:
        """
        Send an emoji reaction to a message via Evolution API.
        Send empty string as emoji to remove reaction.

        Args:
            phone_number: Phone number associated with the chat.
            message_id: Evolution API message ID to react to.
            emoji: Emoji string (e.g. "👍") or "" to remove.
            from_me: Whether the message being reacted to was sent by us.

        Returns:
            API response dict, or None on failure.
        """
        clean_number = phone_number.lstrip("+")
        url = self._build_url("message/sendReaction")
        payload = {
            "key": {
                "remoteJid": f"{clean_number}@s.whatsapp.net",
                "fromMe": from_me,
                "id": message_id,
            },
            "reaction": emoji,
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
            logger.info("Sent reaction %s to message %s", emoji, message_id)
            return data
        except requests.RequestException as exc:
            logger.error("Failed to send reaction to message %s: %s", message_id, exc)
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
        from core.models import EmailConfig
        try:
            config = EmailConfig.get_solo()
            self.host = config.imap_host or "imap.gmail.com"
            self.port = config.imap_port or 993
            self.user = config.imap_user or ""
            self.password = config.imap_password or ""
        except Exception as exc:
            logger.error("Failed to load EmailConfig: %s", exc)
            self.host = "imap.gmail.com"
            self.port = 993
            self.user = ""
            self.password = ""

    def connect(self) -> Optional[imaplib.IMAP4_SSL]:
        if not self.user or not self.password:
            logger.warning("IMAP credentials are not configured. Cannot fetch emails.")
            return None
        try:
            mail = imaplib.IMAP4_SSL(self.host, port=self.port)
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
                        
                        # Extract Priority headers
                        importance_header = self._decode_str(msg.get("Importance", ""))
                        x_priority_header = self._decode_str(msg.get("X-Priority", ""))

                        # Extract Auto-Response headers to prevent loops
                        auto_submitted = self._decode_str(msg.get("Auto-Submitted", ""))
                        x_auto_response_suppress = self._decode_str(msg.get("X-Auto-Response-Suppress", ""))

                        yield {
                            "from": from_,
                            "subject": subject,
                            "text": text_content,
                            "html": html_content,
                            "attachments": attachments,
                            "message_id": message_id_header,
                            "importance": importance_header,
                            "x_priority": x_priority_header,
                            "auto_submitted": auto_submitted,
                            "x_auto_response_suppress": x_auto_response_suppress,
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
