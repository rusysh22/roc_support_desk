import logging
from typing import Any, Optional
from django.conf import settings

logger = logging.getLogger(__name__)

def parse_evolution_webhook(payload: dict[str, Any]) -> Optional[dict[str, Any]]:
    """
    Parses Evolution API webhook payload and safely extracts required components.
    Returns None if the message should be ignored (e.g., from bot, group, status).
    """
    try:
        data = payload.get("data", payload)
        key = data.get("key", {})
        message = data.get("message", {})

        # 5. Message ID Extraction
        message_id = key.get("id")
        if not message_id:
            logger.info("Ignored Evolution webhook: missing message ID.")
            return None

        # 2. Prevent Bot Self-Messages
        from_me = key.get("fromMe", False)
        if from_me:
            logger.info(f"Ignored Evolution webhook {message_id}: originated from bot (fromMe=True).")
            return None

        # Handle LID Addressing
        # WhatsApp Linked Device IDs (LID) use @lid suffix and are NOT real
        # phone numbers. We must resolve them to real @s.whatsapp.net JIDs.
        #
        # Strategy: pick the BEST candidate from remoteJid / remoteJidAlt,
        # preferring any @s.whatsapp.net JID over @lid JIDs.
        remote_jid_primary = key.get("remoteJid", "")
        remote_jid_alt = key.get("remoteJidAlt", "")

        def _is_phone_jid(jid: str) -> bool:
            """Return True if JID looks like a real phone@s.whatsapp.net."""
            if not jid or not jid.endswith("@s.whatsapp.net"):
                return False
            num = jid.split("@")[0]
            return num.isdigit() and 7 <= len(num) <= 15

        # Prefer whichever candidate is a valid phone JID
        if _is_phone_jid(remote_jid_primary):
            remote_jid = remote_jid_primary
        elif _is_phone_jid(remote_jid_alt):
            remote_jid = remote_jid_alt
        else:
            # Neither is a valid phone JID — take whichever is available
            # (will be resolved via DB fallback below)
            remote_jid = remote_jid_primary or remote_jid_alt

        if not remote_jid:
            logger.info(f"Ignored Evolution webhook {message_id}: missing remoteJid/remoteJidAlt.")
            return None

        logger.debug(
            f"Webhook {message_id}: remoteJid={remote_jid_primary}, "
            f"remoteJidAlt={remote_jid_alt}, selected={remote_jid}"
        )

        # 3. Ignore Unsupported Chat Types
        if remote_jid.endswith("@g.us"):
            logger.info(f"Ignored Evolution webhook {message_id}: group message ({remote_jid}).")
            return None

        if remote_jid == "status@broadcast":
            logger.info(f"Ignored Evolution webhook {message_id}: status broadcast.")
            return None

        # Attempt Database Fallback for LID or non-phone JIDs
        if remote_jid.endswith("@lid"):
            logger.info(f"LID address detected ({remote_jid}). Attempting Database Fallback...")

            from gateways.services import EvolutionAPIService
            svc = EvolutionAPIService()
            fallback_chat_data = svc.find_latest_chat(remote_jid)

            if fallback_chat_data and "remoteJid" in fallback_chat_data:
                resolved = fallback_chat_data["remoteJid"]
                if _is_phone_jid(resolved):
                    logger.info(f"Fallback successful. Resolved LID {remote_jid} -> {resolved}")
                    remote_jid = resolved
                else:
                    logger.warning(
                        f"Ignored Evolution webhook {message_id}: "
                        f"Fallback returned non-phone JID ({resolved}) for LID ({remote_jid})."
                    )
                    return None
            else:
                logger.warning(f"Ignored Evolution webhook {message_id}: Fallback failed for LID ({remote_jid}).")
                return None

        # Extract raw number from the resolved JID
        raw_number = remote_jid.split("@")[0]

        # Ensure it's a valid numeric phone number
        if not raw_number.isdigit():
            logger.warning(f"Ignored Evolution webhook {message_id}: non-numeric sender ID ({raw_number}).")
            return None

        # Validate E.164 length (7-15 digits). Numbers outside this range
        # are likely LID identifiers that slipped through (e.g. a numeric
        # LID without the @lid suffix).
        if not (7 <= len(raw_number) <= 15):
            logger.info(
                f"Suspected LID number detected ({raw_number}, {len(raw_number)} digits). "
                f"Attempting to resolve real phone number via Evolution API..."
            )
            from gateways.services import EvolutionAPIService
            svc = EvolutionAPIService()
            fallback_chat_data = svc.find_latest_chat(f"{raw_number}@s.whatsapp.net")
            if not fallback_chat_data:
                fallback_chat_data = svc.find_latest_chat(f"{raw_number}@lid")

            if fallback_chat_data:
                resolved_jid = fallback_chat_data.get("remoteJid", "")
                resolved_number = resolved_jid.split("@")[0]
                if resolved_number.isdigit() and 7 <= len(resolved_number) <= 15:
                    logger.info(f"Resolved LID {raw_number} -> {resolved_number}")
                    raw_number = resolved_number
                else:
                    logger.warning(f"Ignored Evolution webhook {message_id}: could not resolve LID number ({raw_number}).")
                    return None
            else:
                logger.warning(f"Ignored Evolution webhook {message_id}: invalid phone number length ({raw_number}, {len(raw_number)} digits).")
                return None

        sender_number = f"+{raw_number}"

        # Also ignore when sender number equals configured instance number
        instance_number_setting = getattr(settings, "EVOLUTION_INSTANCE_NUMBER", None)
        if instance_number_setting and sender_number.lstrip("+") == str(instance_number_setting).lstrip("+"):
            logger.info(f"Ignored Evolution webhook {message_id}: sender matches configured EVOLUTION_INSTANCE_NUMBER.")
            return None

        # Extract Sender Name
        sender_name = None
        push_name = data.get("pushName")
        if push_name and str(push_name).strip():
            sender_name = str(push_name).strip()
        else:
            contact = payload.get("contact", {}) or data.get("contact", {})
            name = contact.get("notify") or contact.get("name")
            if name and str(name).strip():
                sender_name = str(name).strip()
            else:
                msg_name = message.get("pushName")
                if msg_name and str(msg_name).strip():
                    sender_name = str(msg_name).strip()

        # 4. Message Text Extraction (Fallback Order)
        message_text = None
        if "conversation" in message and isinstance(message["conversation"], str):
            message_text = message["conversation"]
        elif "extendedTextMessage" in message:
            ext = message["extendedTextMessage"]
            if "text" in ext and isinstance(ext["text"], str):
                message_text = ext["text"]
        elif "imageMessage" in message:
            img = message["imageMessage"]
            if "caption" in img and isinstance(img["caption"], str):
                message_text = img["caption"]
        elif "videoMessage" in message:
            vid = message["videoMessage"]
            if "caption" in vid and isinstance(vid["caption"], str):
                message_text = vid["caption"]
        elif "buttonsResponseMessage" in message:
            btn = message["buttonsResponseMessage"]
            if "selectedButtonId" in btn and isinstance(btn["selectedButtonId"], str):
                message_text = btn["selectedButtonId"]
        elif "listResponseMessage" in message:
            lst = message["listResponseMessage"]
            if "singleSelectReply" in lst and "selectedRowId" in lst["singleSelectReply"]:
                message_text = lst["singleSelectReply"]["selectedRowId"]

        # 6. Media Metadata & Quoted ID
        media_info = None
        quoted_id = None
        
        for media_key in ("imageMessage", "videoMessage", "documentMessage", "audioMessage", "stickerMessage"):
            if media_key in message:
                media = message[media_key]
                mime_type = media.get("mimetype", "application/octet-stream")
                ext = mime_type.split("/")[-1].split(";")[0]
                file_name = media.get("fileName", f"attachment.{ext}")
                media_info = {
                    "type": media_key,
                    "mime_type": mime_type,
                    "filename": file_name,
                    "message_id": message_id,
                }
                if "contextInfo" in media:
                    quoted_id = media["contextInfo"].get("stanzaId")
                break

        if not quoted_id:
            ext = message.get("extendedTextMessage", {})
            if ext and "contextInfo" in ext:
                quoted_id = ext["contextInfo"].get("stanzaId")

        # 7. Output Structure
        result = {
            "sender_number": sender_number,
            "sender_name": sender_name,
            "message_text": message_text,
            "message_id": message_id,
            "from_me": from_me,
            "remote_jid": remote_jid,
            "media": media_info,
        }
        
        if quoted_id:
            result["quoted_id"] = quoted_id
            
        return result

    except Exception as exc:
        logger.exception("Error parsing Evolution webhook payload: %s", exc)
        return None
