import sys
import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "roc_desk.settings")
django.setup()

from gateways.services import EvolutionAPIService

payload = {
  "local": "p.sendData-Webhook",
  "url": "http://host.docker.internal:8000/api/gateways/evolution/webhook/messages-upsert",
  "event": "messages.upsert",
  "instance": "helpdesk-wa-final",
  "data": {
    "key": {
      "remoteJid": "251053790478340@lid",
      "fromMe": False,
      "id": "AC78DDAC60B0BC0763F6D4EF195E615C"
    },
    "pushName": None,
    "status": "DELIVERY_ACK",
    "message": {
      "imageMessage": {
        "mimetype": "image/jpeg",
        "caption": "Tolong dibantu case berikut ini",
      },
      "messageContextInfo": {  }
    },
    "contextInfo": { "mentionedJid": [], "groupMentions": [], "pairedMediaType": 0 },
    "messageType": "imageMessage",
    "messageTimestamp": 1772251608,
    "instanceId": "2a8ea9d2-4927-4a11-8f35-38813ba24e23",
    "source": "android"
  },
  "destination": "http://host.docker.internal:8000/api/gateways/evolution/webhook",
  "date_time": "2026-02-28T01:06:58.425Z",
  "sender": "6285808258482@s.whatsapp.net",
  "server_url": "https://genaro-seminarial-stefania.ngrok-free.dev",
  "apikey": "53B747ED-DB10-4BA4-B363-391700B44A04"
}

svc = EvolutionAPIService()

print("Phone:", svc.extract_sender_phone(payload))
print("Name:", svc.extract_sender_name(payload))
print("Message:", svc.extract_message_body(payload))
print("Media:", svc.extract_media_info(payload))
