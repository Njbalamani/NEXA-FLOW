from __future__ import annotations

import json

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer


@database_sync_to_async
def _can_access_workflow(user_id: int, workflow_id: int) -> bool:
    from apps.accounts.models import User
    from apps.workflows.models import Workflow

    user = User.objects.filter(pk=user_id, is_active=True).first()
    if not user:
        return False
    if user.is_superuser:
        return Workflow.objects.filter(pk=workflow_id).exists()
    return Workflow.objects.filter(
        pk=workflow_id,
        organization__members__user_id=user_id,
        organization__members__is_active=True,
    ).exists()


class WorkflowConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        raw_id = self.scope["url_route"]["kwargs"]["workflow_id"]
        try:
            workflow_id = int(raw_id)
        except (TypeError, ValueError):
            await self.close(code=4404)
            return
        user = self.scope.get("user")
        if not user or not user.is_authenticated:
            await self.close(code=4401)
            return
        if not await _can_access_workflow(user.pk, workflow_id):
            await self.close(code=4403)
            return
        self.workflow_id = workflow_id
        self.group_name = f"workflow_{workflow_id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def workflow_event(self, event):
        await self.send(text_data=json.dumps({"type": "workflow.event", **event.get("event", {})}))
