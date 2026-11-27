from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone


class Integration(models.Model):
    PROVIDER_CHOICES = (
        ("email", "Email service"),
        ("webhook", "Webhook"),
        ("google", "Google services"),
        ("slack", "Slack"),
        ("rest_api", "External REST API"),
        ("ai", "AI provider"),
    )

    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="integrations")
    name = models.CharField(max_length=160)
    provider = models.CharField(max_length=30, choices=PROVIDER_CHOICES)
    config = models.JSONField(default=dict, blank=True)
    credentials_encrypted = models.TextField(blank=True)
    credential_ref = models.CharField(
        max_length=120,
        blank=True,
        help_text="Name of an environment variable holding credentials. Never paste secrets into the browser.",
    )
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="created_integrations"
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["organization", "name"], name="uniq_integration_org_name")]
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.provider})"


class WebhookDelivery(models.Model):
    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="webhook_deliveries")
    integration = models.ForeignKey(Integration, on_delete=models.SET_NULL, null=True, blank=True, related_name="deliveries")
    url = models.URLField(max_length=500)
    event = models.CharField(max_length=120, default="test")
    payload = models.JSONField(default=dict, blank=True)
    status_code = models.IntegerField(null=True, blank=True)
    success = models.BooleanField(default=False)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["-created_at"]
