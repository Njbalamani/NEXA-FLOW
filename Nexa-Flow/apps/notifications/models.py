from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone


class Notification(models.Model):
    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="notifications")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name="notifications"
    )
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    category = models.CharField(max_length=60, default="general")
    channel = models.CharField(max_length=20, default="in_app")
    data = models.JSONField(default=dict, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "read_at"]),
            models.Index(fields=["organization", "created_at"]),
        ]

    def __str__(self):
        return self.title

    @property
    def is_read(self):
        return bool(self.read_at)
