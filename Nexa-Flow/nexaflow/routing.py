from django.urls import path

from apps.api.consumers import WorkflowConsumer

websocket_urlpatterns = [
    path("ws/workflows/<int:workflow_id>/", WorkflowConsumer.as_asgi()),
]
