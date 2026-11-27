from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.utils import unique_slug


def generate_webhook_token() -> str:
    return uuid.uuid4().hex


class Workflow(models.Model):
    STATUS_CHOICES = (
        ("draft", "Draft"),
        ("active", "Active"),
        ("paused", "Paused"),
        ("archived", "Archived"),
    )

    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="workflows")
    workspace = models.ForeignKey(
        "organizations.Workspace", on_delete=models.SET_NULL, null=True, blank=True, related_name="workflows"
    )
    name = models.CharField(max_length=180)
    slug = models.SlugField(max_length=200)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="draft")
    definition = models.JSONField(default=dict, blank=True)
    version = models.PositiveIntegerField(default=1)
    webhook_token = models.CharField(max_length=64, unique=True, default=generate_webhook_token)
    is_template = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="created_workflows"
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)
    last_executed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["organization", "slug"], name="uniq_workflow_org_slug")]
        ordering = ["-updated_at"]
        indexes = [models.Index(fields=["organization", "status"])]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slug(Workflow, self.name, {"organization_id": self.organization_id})
        super().save(*args, **kwargs)

    @property
    def node_count(self):
        return self.nodes.count()

    @property
    def execution_count(self):
        return self.executions.count()


class WorkflowVersion(models.Model):
    workflow = models.ForeignKey(Workflow, on_delete=models.CASCADE, related_name="versions")
    version = models.PositiveIntegerField()
    definition = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="workflow_versions"
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["-version"]
        constraints = [models.UniqueConstraint(fields=["workflow", "version"], name="uniq_workflow_version")]
        indexes = [models.Index(fields=["workflow", "created_at"])]

    def __str__(self):
        return f"{self.workflow.name} v{self.version}"


class WorkflowNode(models.Model):
    NODE_TYPES = (
        ("trigger", "Trigger"),
        ("condition", "Condition"),
        ("action", "Action"),
        ("delay", "Delay"),
        ("transform", "Transform"),
        ("webhook", "Webhook"),
    )

    workflow = models.ForeignKey(Workflow, on_delete=models.CASCADE, related_name="nodes")
    key = models.CharField(max_length=80)
    type = models.CharField(max_length=20, choices=NODE_TYPES)
    name = models.CharField(max_length=160)
    config = models.JSONField(default=dict, blank=True)
    position_x = models.IntegerField(default=0)
    position_y = models.IntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["workflow", "key"], name="uniq_node_workflow_key")]
        ordering = ["position_x", "position_y"]

    def __str__(self):
        return f"{self.name} ({self.type})"


class WorkflowConnection(models.Model):
    workflow = models.ForeignKey(Workflow, on_delete=models.CASCADE, related_name="connections")
    source = models.ForeignKey(WorkflowNode, on_delete=models.CASCADE, related_name="outgoing")
    target = models.ForeignKey(WorkflowNode, on_delete=models.CASCADE, related_name="incoming")
    source_handle = models.CharField(max_length=40, default="out")
    target_handle = models.CharField(max_length=40, default="in")
    label = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workflow", "source", "target", "source_handle"], name="uniq_connection_workflow"
            )
        ]

    def __str__(self):
        return f"{self.source.key}:{self.source_handle} → {self.target.key}"


class WorkflowTrigger(models.Model):
    TRIGGER_TYPES = (
        ("manual", "Manual"),
        ("schedule", "Scheduled"),
        ("webhook", "Webhook"),
        ("api", "API"),
        ("event", "Event"),
    )

    workflow = models.ForeignKey(Workflow, on_delete=models.CASCADE, related_name="triggers")
    name = models.CharField(max_length=160)
    type = models.CharField(max_length=20, choices=TRIGGER_TYPES, default="manual")
    config = models.JSONField(default=dict, blank=True)
    webhook_secret = models.CharField(max_length=64, default=generate_webhook_token)
    is_active = models.BooleanField(default=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.type})"


class WorkflowExecution(models.Model):
    STATUS_CHOICES = (
        ("queued", "Queued"),
        ("running", "Running"),
        ("success", "Success"),
        ("failed", "Failed"),
        ("cancelled", "Cancelled"),
        ("timed_out", "Timed out"),
    )

    workflow = models.ForeignKey(Workflow, on_delete=models.CASCADE, related_name="executions")
    trigger = models.ForeignKey(WorkflowTrigger, on_delete=models.SET_NULL, null=True, blank=True, related_name="executions")
    trigger_type = models.CharField(max_length=20, default="manual")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="queued")
    input = models.JSONField(default=dict, blank=True)
    context = models.JSONField(default=dict, blank=True)
    result = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True)
    attempts = models.PositiveIntegerField(default=1)
    duration_ms = models.PositiveIntegerField(default=0)
    celery_task_id = models.CharField(max_length=80, blank=True)
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="triggered_executions"
    )
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["workflow", "status"]),
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["triggered_by", "created_at"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"{self.workflow.name} #{self.pk} ({self.status})"

    @property
    def duration(self):
        if not self.started_at or not self.finished_at:
            return 0
        return int((self.finished_at - self.started_at).total_seconds() * 1000)


class ExecutionStep(models.Model):
    STATUS_CHOICES = (
        ("running", "Running"),
        ("success", "Success"),
        ("failed", "Failed"),
        ("skipped", "Skipped"),
    )

    execution = models.ForeignKey(WorkflowExecution, on_delete=models.CASCADE, related_name="steps")
    node = models.ForeignKey(WorkflowNode, on_delete=models.SET_NULL, null=True, blank=True, related_name="execution_steps")
    node_key = models.CharField(max_length=80)
    node_name = models.CharField(max_length=160)
    node_type = models.CharField(max_length=20)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="running")
    input = models.JSONField(default=dict, blank=True)
    output = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True)
    attempt = models.PositiveIntegerField(default=1)
    started_at = models.DateTimeField(default=timezone.now, editable=False)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["started_at"]
        constraints = [
            models.UniqueConstraint(fields=["execution", "node_key"], name="uniq_execution_step_per_node")
        ]
