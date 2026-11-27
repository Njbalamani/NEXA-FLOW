import importlib.util
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "nexaflow.settings")

django_asgi_app = get_asgi_application()

if importlib.util.find_spec("channels"):
    from channels.auth import AuthMiddlewareStack
    from channels.routing import ProtocolTypeRouter, URLRouter

    from .routing import websocket_urlpatterns

    application = ProtocolTypeRouter(
        {
            "http": django_asgi_app,
            "websocket": AuthMiddlewareStack(URLRouter(websocket_urlpatterns)),
        }
    )
else:  # pragma: no cover - channels is optional at runtime
    application = django_asgi_app
