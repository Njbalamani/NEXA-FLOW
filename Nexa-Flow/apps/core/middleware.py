import threading
import uuid

_local = threading.local()


def get_current_request():
    return getattr(_local, "request", None)


class RequestIdMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        supplied = str(request.META.get("HTTP_X_REQUEST_ID", "")).strip()[:64]
        request.request_id = supplied or uuid.uuid4().hex
        response = self.get_response(request)
        response.setdefault("X-Request-ID", request.request_id)
        return response


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        headers = {
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "strict-origin-when-cross-origin",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
            "Cross-Origin-Opener-Policy": "same-origin",
            "Cross-Origin-Resource-Policy": "same-origin",
            "X-Permitted-Cross-Domain-Policies": "none",
        }
        for name, value in headers.items():
            response.setdefault(name, value)
        return response


class RequestContextMiddleware:
    """Make the active request available to audit logging and services."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        _local.request = request
        try:
            return self.get_response(request)
        finally:
            _local.request = None
