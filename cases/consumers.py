import asyncio
import json
import time

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer


class ChatConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for the live chat panel.

    Each ticket gets its own channel group: chat_<case_uuid>.
    Both the portal user and support staff subscribe so messages are
    pushed to all connected clients in real time.

    Authentication:
      - Authenticated users (staff or portal login) identified via
        Django session middleware (AuthMiddlewareStack in asgi.py).
      - Guest users supply their guest_token as a query param:
        ws://host/ws/chat/<uuid>/?token=<guest_token>
    """

    HEARTBEAT_INTERVAL = 30  # seconds between server-sent ping frames
    TYPING_COOLDOWN = 2      # minimum seconds between forwarded typing events

    async def connect(self):
        self.case_uuid = self.scope["url_route"]["kwargs"]["case_uuid"]
        self.group_name = f"chat_{self.case_uuid}"
        self._last_typing_at: float = 0.0
        self.is_staff = False

        if not await self._can_access():
            await self.close(code=4003)
            return

        self.is_staff = await self._check_is_staff()
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        self._heartbeat_task = asyncio.ensure_future(self._heartbeat())

        if self.is_staff:
            await self._set_staff_online_cache(True)
            await self.channel_layer.group_send(
                self.group_name,
                {"type": "chat.staff_presence", "online": True},
            )

    async def disconnect(self, code):
        if hasattr(self, "_heartbeat_task"):
            self._heartbeat_task.cancel()
        await self.channel_layer.group_discard(self.group_name, self.channel_name)
        if getattr(self, "is_staff", False):
            await self._set_staff_online_cache(False)
            await self.channel_layer.group_send(
                self.group_name,
                {"type": "chat.staff_presence", "online": False},
            )

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
        except (json.JSONDecodeError, ValueError):
            return

        event_type = data.get("type")

        if event_type == "pong":
            # Client acknowledged our heartbeat ping — no-op.
            return

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
                    "sender_email": await self._get_sender_email(message),
                    "sent_at": message.sent_at.isoformat(),
                    "is_system": message.is_system,
                    "quoted_message_id": str(message.quoted_message_id) if message.quoted_message_id else None,
                    "quoted_body": quoted_body,
                    "quoted_sender": quoted_sender,
                },
            )

            # Inbound = portal user sent to staff. Delay 15 s so real-time
            # delivery is attempted first; Celery checks has_unread_messages.
            if message.direction == "IN":
                await self._dispatch_offline_notification(str(message.id))

        elif event_type == "typing":
            now = time.monotonic()
            # Server-side guard: never forward faster than TYPING_COOLDOWN seconds.
            if now - self._last_typing_at < self.TYPING_COOLDOWN:
                return
            self._last_typing_at = now
            sender_name = await self._get_current_user_name()
            await self.channel_layer.group_send(
                self.group_name,
                {
                    "type": "chat.typing",
                    "sender_channel": self.channel_name,
                    "sender_name": sender_name,
                },
            )

        elif event_type == "messages_read":
            latest_message_id = (data.get("latest_message_id") or "").strip()
            if latest_message_id:
                # Broadcast read receipt immediately for real-time "Read" indicator.
                await self.channel_layer.group_send(
                    self.group_name,
                    {
                        "type": "chat.read_receipt",
                        "latest_message_id": latest_message_id,
                        "reader_channel": self.channel_name,
                    },
                )
                # Persist to DB via Celery (decoupled from real-time path).
                await self._dispatch_batch_read(latest_message_id)

    # ----------------------------------------------------------------
    # Heartbeat — keeps alive and surfaces silent disconnections
    # ----------------------------------------------------------------

    async def _heartbeat(self):
        while True:
            await asyncio.sleep(self.HEARTBEAT_INTERVAL)
            try:
                await self.send(text_data=json.dumps({"type": "ping"}))
                if self.is_staff:
                    # Refresh TTL so portal users see staff as online while they are active.
                    await self._set_staff_online_cache(True)
            except Exception:
                break

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
            "sender_email": event.get("sender_email", ""),
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
            "sender_email": event.get("sender_email", ""),
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

    async def chat_read_receipt(self, event):
        # Forward to all connected clients — portal user sees "Read" indicator.
        await self.send(text_data=json.dumps({
            "type": "read_receipt",
            "latest_message_id": event["latest_message_id"],
        }))

    async def chat_staff_presence(self, event):
        await self.send(text_data=json.dumps({
            "type": "staff_presence",
            "online": event["online"],
        }))

    # ----------------------------------------------------------------
    # DB helpers
    # ----------------------------------------------------------------

    @database_sync_to_async
    def _can_access(self):
        from .models import CaseRecord
        from django.contrib.auth.models import AnonymousUser

        try:
            case = CaseRecord.objects.select_related("category").get(id=self.case_uuid)
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
                if getattr(case.category, 'is_confidential', False):
                    if user.role_access != User.RoleAccess.SUPERADMIN and not getattr(user, 'can_handle_confidential', False):
                        return False
                return True
            if user.role_access == User.RoleAccess.PORTALUSER:
                email = getattr(user, "email", "") or ""
                is_requester = email.lower() == (case.requester_email or "").lower()
                is_follower = case.followers.filter(id=user.id).exists()
                return is_requester or is_follower

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
            # Find the Employee record for the actual sending user, not just the requester
            sender_email = None
            if user and not isinstance(user, AnonymousUser) and user.is_authenticated:
                sender_email = getattr(user, "email", "") or ""
            if not sender_email:
                sender_email = case.requester_email
            sender_employee = Employee.objects.filter(
                email__iexact=sender_email
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
            CaseRecord.objects.filter(id=self.case_uuid).update(has_unread_messages=True)

        # Dispatch user notifications (async-safe via Celery)
        try:
            self._dispatch_chat_notifications(case, msg, user, body)
        except Exception:
            pass  # Notification failure must never block chat

        return msg

    def _dispatch_chat_notifications(self, case, msg, sender_user, body):
        """Dispatch notifications to all participants except the sender."""
        from core.notifications import notify_user
        from django.contrib.auth.models import AnonymousUser
        import re

        sender_email = ""
        if sender_user and not isinstance(sender_user, AnonymousUser) and sender_user.is_authenticated:
            sender_email = (sender_user.email or "").lower()

        sender_name = ""
        if msg.sender_staff:
            sender_name = msg.sender_staff.get_full_name() or msg.sender_staff.username
        elif msg.sender_employee:
            sender_name = msg.sender_employee.full_name or "User"
        else:
            sender_name = case.requester_name or "User"

        # Build context
        context = {
            "ticket_id": str(case.id),
            "ticket_subject": case.subject or "",
            "sender_name": sender_name,
            "message_preview": re.sub(r"<[^>]+>", "", body or "")[:200],
            "ticket_url": f"/portal/chat/{case.id}/",
        }

        # Collect all participants (requester user + followers)
        from core.models import User
        participants = []

        # Requester (if they have a User account)
        if case.requester_email:
            req_user = User.objects.filter(email__iexact=case.requester_email).first()
            if req_user:
                participants.append(req_user)

        # Followers
        for follower in case.followers.all():
            if follower not in participants:
                participants.append(follower)

        # Check for @mentions in the body
        mentioned_names = set()
        if body:
            mention_pattern = re.compile(r'data-mention-id="([^"]+)"', re.IGNORECASE)
            for match in mention_pattern.finditer(body):
                mentioned_names.add(match.group(1))

        for participant in participants:
            # Skip the sender
            if sender_email and (participant.email or "").lower() == sender_email:
                continue

            # Check if this user was @mentioned
            p_name = participant.username or participant.get_full_name() or ""
            if p_name and p_name in mentioned_names:
                notify_user(participant, "mention", {**context, "event_type": "mention"})
            else:
                notify_user(participant, "new_message", context)

    @database_sync_to_async
    def _get_sender_name(self, message):
        if message.sender_staff:
            name = message.sender_staff.get_full_name()
            return name or getattr(message.sender_staff, "username", None) or str(message.sender_staff)
        if message.sender_employee:
            return message.sender_employee.full_name or "Unknown"
        # Fallback: try scope user (covers followers without Employee record)
        from django.contrib.auth.models import AnonymousUser
        user = self.scope.get("user")
        if user and not isinstance(user, AnonymousUser) and user.is_authenticated:
            name = user.get_full_name()
            if name:
                return name
        try:
            return message.case.requester_name or "User"
        except Exception:
            return "User"

    @database_sync_to_async
    def _get_sender_email(self, message):
        if message.sender_staff:
            return (getattr(message.sender_staff, "email", "") or "").lower()
        if message.sender_employee:
            return (getattr(message.sender_employee, "email", "") or "").lower()
        return ""

    @database_sync_to_async
    def _get_current_user_name(self):
        from django.contrib.auth.models import AnonymousUser
        user = self.scope.get("user")
        if user and not isinstance(user, AnonymousUser) and user.is_authenticated:
            name = user.get_full_name()
            return name or getattr(user, "username", None) or str(user)
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

    @database_sync_to_async
    def _check_is_staff(self):
        from django.contrib.auth.models import AnonymousUser
        from core.models import User
        user = self.scope.get("user")
        return bool(
            user
            and not isinstance(user, AnonymousUser)
            and user.is_authenticated
            and user.role_access in (
                User.RoleAccess.SUPERADMIN,
                User.RoleAccess.MANAGER,
                User.RoleAccess.SUPPORTDESK,
                User.RoleAccess.AUDITOR,
            )
        )

    @database_sync_to_async
    def _set_staff_online_cache(self, online: bool):
        from django.core.cache import cache
        key = f"staff_online_{self.case_uuid}"
        if online:
            cache.set(key, True, timeout=90)
        else:
            cache.delete(key)

    @database_sync_to_async
    def _dispatch_offline_notification(self, message_id):
        from cases.tasks import send_chat_offline_notification
        send_chat_offline_notification.apply_async(
            args=[str(self.case_uuid), message_id],
            countdown=15,
            queue="chat_tasks",
        )

    @database_sync_to_async
    def _dispatch_batch_read(self, latest_message_id):
        from cases.tasks import batch_mark_messages_read
        batch_mark_messages_read.delay(str(self.case_uuid), latest_message_id)
