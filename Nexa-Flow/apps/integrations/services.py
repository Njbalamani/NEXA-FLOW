from __future__ import annotations

import json
import os

import requests
from django.conf import settings
from django.core.mail import send_mail

from apps.audit.services import log_action
from apps.core.crypto import decrypt_credentials, encrypt_credentials, mask_credentials
from apps.core.security import is_safe_outbound_url

from .models import Integration, WebhookDelivery


def set_integration_credentials(integration: Integration, data: dict, user=None):
    integration.credentials_encrypted = encrypt_credentials(data or {})
    integration.save(update_fields=["credentials_encrypted", "updated_at"])
    log_action(
        user,
        "integration.credentials_updated",
        integration,
        {"provider": integration.provider, "keys": list((data or {}).keys())},
        integration.organization,
    )
    return integration


def integration_credentials(integration: Integration) -> dict:
    credentials = decrypt_credentials(integration.credentials_encrypted)
    if integration.credential_ref:
        raw = os.environ.get(integration.credential_ref, "")
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    credentials.update(parsed)
                else:
                    credentials["token"] = str(parsed)
            except json.JSONDecodeError:
                credentials["token"] = raw
    return credentials


def public_integration(integration: Integration) -> dict:
    credentials = integration_credentials(integration)
    return {
        "id": integration.pk,
        "name": integration.name,
        "provider": integration.provider,
        "config": integration.config,
        "credential_ref": integration.credential_ref,
        "is_active": integration.is_active,
        "has_credentials": bool(credentials),
        "credential_fields": list(mask_credentials(credentials).keys()),
        "created_at": integration.created_at.isoformat(),
    }


def test_integration(integration: Integration, user=None) -> WebhookDelivery | dict:
    credentials = integration_credentials(integration)
    if integration.provider == "email":
        send_mail(
            "NexaFlow integration test",
            f"The integration '{integration.name}' is configured correctly.",
            settings.NEXAFLOW["EMAIL_FROM"],
            [user.email] if user and user.email else [settings.NEXAFLOW["EMAIL_FROM"]],
            fail_silently=False,
        )
        log_action(user, "integration.tested", integration, {"provider": "email", "success": True}, integration.organization)
        return {"provider": "email", "success": True, "message": "Test email sent."}

    url = str(integration.config.get("test_url") or credentials.get("webhook_url") or credentials.get("base_url") or "")
    if not url or not is_safe_outbound_url(url):
        raise ValueError("Provide a safe HTTPS test URL or configure credentials first.")

    delivery = WebhookDelivery(
        organization=integration.organization,
        integration=integration,
        url=url,
        event="integration.test",
        payload={"integration_id": integration.pk, "source": "nexaflow"},
    )
    try:
        response = requests.post(url, json=delivery.payload, timeout=10, allow_redirects=False)
        delivery.status_code = response.status_code
        delivery.success = response.status_code < 400
        delivery.error = "" if delivery.success else response.text[:2000]
    except Exception as exc:
        delivery.error = str(exc)[:2000]
    delivery.save()
    log_action(
        user,
        "integration.tested",
        integration,
        {"provider": integration.provider, "success": delivery.success},
        integration.organization,
    )
    return delivery


def retry_webhook_delivery(delivery: WebhookDelivery, user=None) -> WebhookDelivery:
    if not is_safe_outbound_url(delivery.url):
        raise ValueError("Stored delivery URL is not allowed.")

    retry = WebhookDelivery.objects.create(
        organization=delivery.organization,
        integration=delivery.integration,
        url=delivery.url,
        event=delivery.event,
        payload=delivery.payload,
    )
    try:
        response = requests.post(retry.url, json=retry.payload, timeout=10, allow_redirects=False)
        retry.status_code = response.status_code
        retry.success = response.status_code < 400
        retry.error = "" if retry.success else response.text[:2000]
    except Exception as exc:
        retry.error = str(exc)[:2000]
    retry.save()
    log_action(
        user,
        "webhook.delivery_retried",
        delivery.integration or delivery,
        {"delivery_id": delivery.pk, "retry_id": retry.pk, "success": retry.success},
        delivery.organization,
    )
    return retry
