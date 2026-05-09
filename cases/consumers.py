import json
import uuid

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.utils import timezone


class ChatConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for the live chat panel.

    Each ticket gets its own channel group: chat_<case_uuid>.
    Both the public user and support staff subscribe to this group so
    messages sent by either side are pushed to all connected clients
    in real time.

    Authentication:
      - Authenticated users (staff or portal login) are identified via
        Django's session middleware (AuthMiddlewareStack in asgi.py).
      - Guest users must supply their guest_token as a query parameter:
        ws://host/ws/chat/<uuid>/?token=<guest_token>
    """

    async def connect(self):
        self.case_uuid = self.scope["url_route"]["kwargs"]["case_uuid"]
        self.group_name = f"chat_{self.case_uuid}"

        if not await self._can_access():
            await self.close(code=4003)
            return

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
        except (json.JSONDecodeError, ValueError):
            return

        event_type = data.get("type")

        if event_type == "chat_message":
            body = (data.get("body") or "").strip()
            quoted_message_id = (data.get("quoted_message_id") or "").strip() or None
            if not body:
                return
            message = await self._save_message(body, quoted_message_id)
            if message is None:
                return

            quoted_body, quoted_sender = await self._get_quoted_info(message)

            await self.channel_layer.group_send(
                self.group_name,
                {
                    "type": "chat.message",
                    "message_id": str(message.id),
                    "body": message.body,
                    "direction": message.direction,
                    "sender_name": await self._get_sender_name(message),
                    "sent_at": message.sent_at.isoformat(),
                    "is_system": message.is_system,
                    "quoted_message_id": str(message.quoted_message_id) if message.quoted_message_id else None,
                    "quoted_body": quoted_body,
                    "quoted_sender": quoted_sender,
                },
            )

        elif event_type == "typing":
            sender_name = await self._get_current_user_name()
            await self.channel_layer.group_send(
                self.group_name,
                {
                    "type": "chat.typing",
                    "sender_channel": self.channel_name,
                    "sender_name": sender_name,
                },
            )

    # ----------------------------------------------------------------
    # Group event handlers
    # ----------------------------------------------------------------

    async def chat_message(self, event):
        await self.send(text_data=json.dumps({
            "type": "chat_message",
            "message_id": event["message_id"],
            "body": event["body"],
            "direction": event["direction"],
            "sender_name": event["sender_name"],
            "sent_at": event["sent_at"],
            "is_system": event["is_system"],
            "quoted_message_id": event.get("quoted_message_id"),
            "quoted_body": event.get("quoted_body"),
            "quoted_sender": event.get("quoted_sender"),
        }))

    async def chat_typing(self, event):
        if event["sender_channel"] == self.channel_name:
            return
        await self.send(text_data=json.dumps({
            "type": "typing",
            "sender_name": event.get("sender_name", ""),
        }))

    async def chat_attachment(self, event):
        await self.send(text_data=json.dumps({
            "type": "chat_attachment",
            "message_id": event["message_id"],
            "file_url": event["file_url"],
            "filename": event["filename"],
            "mime_type": event["mime_type"],
            "is_image": event["is_image"],
            "sent_at": event["sent_at"],
            "direction": event["direction"],
            "sender_name": event["sender_name"],
        }))

    async def chat_status_update(self, event):
        await self.send(text_data=json.dumps({
            "type": "status_update",
            "status": event["status"],
            "status_display": event["status_display"],
        }))

    async def chat_delete(self, event):
        await self.send(text_data=json.dumps({
            "type": "chat_delete",
            "message_id": event["message_id"],
        }))

    # ----------------------------------------------------------------
    # Helpers
    # ----------------------------------------------------------------

    @database_sync_to_async
    def _can_access(self):
        from .models import CaseRecord
        from django.contrib.auth.models import AnonymousUser

        try:
            case = CaseRecord.objects.select_related("category").get(
                id=self.case_uuid
            )
        except (CaseRecord.DoesNotExist, Exception):
            return False

        user = self.scope.get("user")

        if user and not isinstance(user, AnonymousUser) and user.is_authenticated:
            from core.models import User
            if user.role_access in (
                User.RoleAccess.SUPERADMIN,
                User.RoleAccess.MANAGER,
                User.RoleAccess.SUPPORTDESK,
                User.RoleAccess.AUDITOR,
            ):
                return True
            if user.role_access == User.RoleAccess.PORTALUSER:
                email = getattr(user, "email", "") or ""
                return email.lower() == (case.requester_email or "").lower()

        query_string = self.scope.get("query_string", b"").decode()
        params = dict(
            pair.split("=", 1)
            for pair in query_string.split("&")
            if "=" in pair
        )
        token = params.get("token", "")
        if token and case.guest_token and token == case.guest_token:
            return True

        return False

    @database_sync_to_async
    def _save_message(self, body, quoted_message_id=None):
        from .models import CaseRecord, Message
        from core.models import Employee
        from django.contrib.auth.models import AnonymousUser

        try:
            case = CaseRecord.objects.get(id=self.case_uuid)
        except CaseRecord.DoesNotExist:
            return None

        user = self.scope.get("user")
        is_staff = (
            user
            and not isinstance(user, AnonymousUser)
            and user.is_authenticated
            and user.role_access in ("SuperAdmin", "Manager", "SupportDesk", "Auditor")
        )

        sender_employee = None
        if not is_staff:
            sender_employee = Employee.objects.filter(
                email__iexact=case.requester_email
            ).first()

        msg = Message.objects.create(
            case=case,
            body=body,
            direction=Message.Direction.OUTBOUND if is_staff else Message.Direction.INBOUND,
            channel=Message.Channel.WEB,
            sender_staff=user if is_staff else None,
            sender_employee=sender_employee,
            is_system=False,
            quoted_message_id=quoted_message_id,
        )

        if not is_staff:
            CaseRecord.objects.filter(id=self.case_uuid).update(
                has_unread_messages=True
            )

        return msg

    @database_sync_to_async
    def _get_sender_name(self, message):
        if message.sender_staff:
            name = message.sender_staff.get_full_name()
            return name or getattr(message.sender_staff, "username", None) or str(message.sender_staff)
        if message.sender_employee:
            return message.sender_employee.full_name or "Unknown"
        # Guest/anonymous — fall back to the case's requester name
        try:
            return message.case.requester_name or "User"
        except Exception:
            return "User"

    @database_sync_to_async
    def _get_current_user_name(self):
        from django.contrib.auth.models import AnonymousUser
        user = self.scope.get("user")
        if user and not isinstance(user, AnonymousUser) and user.is_authenticated:
            name = user.get_full_name()
            return name or getattr(user, "username", None) or str(user)
        # Guest — use case's requester name
        try:
            from .models import CaseRecord
            case = CaseRecord.objects.get(id=self.case_uuid)
            return case.requester_name or "Guest"
        except Exception:
            return "Guest"

    @database_sync_to_async
    def _get_quoted_info(self, message):
        if not message.quoted_message_id:
            return None, None
        try:
            from .models import Message
            qm = Message.objects.select_related(
                "sender_staff", "sender_employee"
            ).get(id=message.quoted_message_id)
            body = qm.body[:120] if not qm.is_deleted else None
            if qm.sender_staff:
                sender = qm.sender_staff.get_full_name() or str(qm.sender_staff)
            elif qm.sender_employee:
                sender = qm.sender_employee.full_name or "User"
            else:
                sender = "System"
            return body, sender
        except Exception:
            return None, None
