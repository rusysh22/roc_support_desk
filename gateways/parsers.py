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

        # Handle LID Addressing: prioritize remoteJidAlt over remoteJid
        remote_jid_alt = key.get("remoteJidAlt", "")
        remote_jid = remote_jid_alt if remote_jid_alt else key.get("remoteJid", "")
        
        if not remote_jid:
            logger.info(f"Ignored Evolution webhook {message_id}: missing remoteJid/remoteJidAlt.")
            return None

        # 3. Ignore Unsupported Chat Types
        if remote_jid.endswith("@g.us"):
            logger.info(f"Ignored Evolution webhook {message_id}: group message ({remote_jid}).")
            return None

        if remote_jid == "status@broadcast":
            logger.info(f"Ignored Evolution webhook {message_id}: status broadcast.")
            return None
            
        # Ignore unresolved LID identifiers or attempt Database Fallback
        if remote_jid.endswith("@lid") and not remote_jid_alt:
            logger.info(f"Unresolved LID address detected ({remote_jid}). Attempting Database Fallback...")
            
            from gateways.services import EvolutionAPIService
            svc = EvolutionAPIService()
            fallback_chat_data = svc.find_latest_chat(remote_jid)
            
            if fallback_chat_data and "remoteJid" in fallback_chat_data:
                # Evolution's chat response usually stores the real JID in the top level remoteJid string
                remote_jid = fallback_chat_data["remoteJid"]
                logger.info(f"Fallback successful. Reassigned LID {key.get('remoteJid')} -> {remote_jid}")
            else:
                logger.warning(f"Ignored Evolution webhook {message_id}: Fallback failed. Unable to resolve LID address ({remote_jid}).")
                return None

        # 1. Correct Sender Extraction
        # Safely split at "@" instead of chaining replaces
        raw_number = remote_jid.split("@")[0]
        
        # Ensure it's a valid numeric phone number before proceeding
        # e.g. prevents saving "+217188090806482"
        if not raw_number.isdigit():
            logger.warning(f"Ignored Evolution webhook {message_id}: non-numeric sender ID ({raw_number}).")
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
                    "file_name": file_name,
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
