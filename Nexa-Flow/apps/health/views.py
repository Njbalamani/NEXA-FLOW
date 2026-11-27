from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from apps.workflows.models import Workflow
from apps.workflows.services import execute_workflow_async
from apps.billing.models import Subscription


@require_GET
def health(request):
    try:
        connection.ensure_connection()
        database = "ok"
    except Exception:
        database = "unavailable"

    cache_key = f"nexaflow:health:{uuid.uuid4().hex}"
    try:
        cache.set(cache_key, "ok", 5)
        cache_status = "ok" if cache.get(cache_key) == "ok" else "unavailable"
    except Exception:
        cache_status = "unavailable"
    finally:
        cache.delete(cache_key)

    celery_status = "eager" if settings.CELERY_TASK_ALWAYS_EAGER else "configured"
    healthy = database == "ok" and cache_status == "ok"
    return JsonResponse(
        {
            "status": "ok" if healthy else "degraded",
            "database": database,
            "cache": cache_status,
            "celery": celery_status,
            "service": "nexaflow",
        }
    )


def _client_ip(request) -> str:
    if settings.NEXAFLOW.get("TRUST_X_FORWARDED_FOR"):
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",", 1)[0].strip()
        if forwarded:
            return forwarded
    return request.META.get("REMOTE_ADDR", "unknown")


def _signature_is_valid(raw_body: bytes, secret: str, supplied: str, timestamp: str = "") -> bool:
    if not supplied:
        return False
    signed_payload = f"{timestamp}.".encode() + raw_body if timestamp else raw_body
    expected = "sha256=" + hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    candidate = supplied.strip()
    if candidate.startswith("sha256="):
        candidate = candidate[7:]
    return hmac.compare_digest(candidate, expected.split("=", 1)[1])


@csrf_exempt
@require_POST
def webhook_dispatch(request, token: str):
    workflow = Workflow.objects.filter(webhook_token=token).select_related("organization").first()
    if not workflow or workflow.status != "active":
        return JsonResponse({"error": "Webhook endpoint not found or inactive."}, status=404)

    raw_body = request.body
    if len(raw_body) > settings.NEXAFLOW["WEBHOOK_MAX_BODY_BYTES"]:
        return JsonResponse({"error": "Payload too large."}, status=413)

    trigger = workflow.triggers.filter(type="webhook", is_active=True).first()
    if not trigger:
        return JsonResponse({"error": "No active webhook trigger."}, status=404)

    rate_key = f"nexaflow:webhook:rate:{workflow.pk}:{_client_ip(request)}"
    rate_count = int(cache.get(rate_key, 0) or 0)
    if rate_count >= settings.NEXAFLOW["WEBHOOK_RATE_LIMIT_PER_MINUTE"]:
        return JsonResponse({"error": "Webhook rate limit exceeded."}, status=429)
    cache.set(rate_key, rate_count + 1, 60)

    signature = request.headers.get("X-NexaFlow-Signature") or request.headers.get("X-Hub-Signature-256", "")
    timestamp = request.headers.get("X-NexaFlow-Timestamp", "").strip()
    nonce = request.headers.get("X-NexaFlow-Nonce", "").strip()
    if timestamp:
        try:
            age = abs(int(time.time()) - int(timestamp))
        except ValueError:
            return JsonResponse({"error": "Invalid webhook timestamp."}, status=401)
        if age > settings.NEXAFLOW["WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS"] or not nonce:
            return JsonResponse({"error": "Stale or incomplete webhook timestamp."}, status=401)
    signed_timestamp = timestamp if nonce else ""
    if not _signature_is_valid(raw_body, trigger.webhook_secret, signature, signed_timestamp):
        return JsonResponse({"error": "Invalid webhook signature."}, status=401)

    replay_key = "nexaflow:webhook:" + hashlib.sha256(
        f"{timestamp}:{nonce}:{signature}".encode()
    ).hexdigest()
    if not cache.add(replay_key, 1, settings.NEXAFLOW["WEBHOOK_REPLAY_TTL_SECONDS"]):
        return JsonResponse({"error": "Duplicate webhook delivery."}, status=409)

    subscription = Subscription.objects.filter(organization=workflow.organization).first()
    from apps.workflows.models import WorkflowExecution
    from django.utils import timezone

    month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    used = WorkflowExecution.objects.filter(workflow__organization=workflow.organization, created_at__gte=month_start).count()
    if subscription and not subscription.allows("executions_per_month", used):
        return JsonResponse({"error": "Execution limit reached for the current plan."}, status=429)

    try:
        payload = json.loads(raw_body.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        payload = {"raw": raw_body.decode("utf-8", errors="replace")}
    if not isinstance(payload, dict):
        payload = {"value": payload}

    execution = execute_workflow_async(
        workflow,
        input_data=payload,
        trigger=trigger,
    )
    return JsonResponse(
        {"accepted": True, "execution_id": execution.pk, "workflow": workflow.slug},
        status=202,
    )
