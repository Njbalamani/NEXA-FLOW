from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone


class Task(models.Model):
    STATUS_CHOICES = (
        ("pending", "Pending"),
        ("in_progress", "In progress"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
    )
    PRIORITY_CHOICES = (
        ("low", "Low"),
        ("medium", "Medium"),
        ("high", "High"),
        ("urgent", "Urgent"),
    )

    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="tasks")
    workspace = models.ForeignKey("organizations.Workspace", on_delete=models.SET_NULL, null=True, blank=True, related_name="tasks")
    workflow = models.ForeignKey("workflows.Workflow", on_delete=models.SET_NULL, null=True, blank=True, related_name="tasks")
    execution = models.ForeignKey(
        "workflows.WorkflowExecution", on_delete=models.SET_NULL, null=True, blank=True, related_name="tasks"
    )
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="assigned_tasks"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="created_tasks"
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default="medium")
    due_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["organization", "status"]),
            models.Index(fields=["organization", "assignee", "status"]),
            models.Index(fields=["organization", "due_at"]),
        ]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if self.status == "completed" and not self.completed_at:
            self.completed_at = timezone.now()
        elif self.status != "completed":
            self.completed_at = None
        super().save(*args, **kwargs)

    @property
    def is_overdue(self):
        return bool(self.due_at and self.due_at < timezone.now() and self.status not in {"completed", "cancelled"})


class TaskEvent(models.Model):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    event = models.CharField(max_length=80)
    data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.task.title}: {self.event}"
