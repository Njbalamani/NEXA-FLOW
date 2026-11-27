from rest_framework.views import exception_handler


def api_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is None:
        return None
    detail = response.data
    if isinstance(detail, dict) and "errors" not in detail:
        response.data = {"error": True, "code": getattr(exc, "code", None) or response.status_code, "detail": detail}
    elif not isinstance(detail, dict):
        response.data = {"error": True, "code": response.status_code, "detail": detail}
    else:
        response.data.setdefault("error", True)
        response.data.setdefault("code", response.status_code)
    return response
