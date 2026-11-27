from __future__ import annotations

from celery import current_app
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.audit.services import log_action
from apps.billing.limits import enforce_limit

from .models import (
    Workflow,
    WorkflowConnection,
    WorkflowExecution,
    WorkflowNode,
    WorkflowTrigger,
    WorkflowVersion,
)
from .validation import WorkflowValidationError, validate_definition


def workflow_definition(workflow: Workflow) -> dict:
    nodes = list(workflow.nodes.all())
    keys = {node.pk: node.key for node in nodes}
    return {
        "id": workflow.pk,
        "name": workflow.name,
        "slug": workflow.slug,
        "description": workflow.description,
        "status": workflow.status,
        "version": workflow.version,
        "nodes": [
            {
                "id": node.pk,
                "key": node.key,
                "type": node.type,
                "name": node.name,
                "config": node.config,
                "position": {"x": node.position_x, "y": node.position_y},
            }
            for node in nodes
        ],
        "connections": [
            {
                "id": connection.pk,
                "source": keys.get(connection.source_id, ""),
                "target": keys.get(connection.target_id, ""),
                "source_handle": connection.source_handle,
                "target_handle": connection.target_handle,
                "label": connection.label,
            }
            for connection in workflow.connections.all()
        ],
        "triggers": [
            {
                "id": trigger.pk,
                "name": trigger.name,
                "type": trigger.type,
                "config": trigger.config,
                "is_active": trigger.is_active,
            }
            for trigger in workflow.triggers.all()
        ],
    }


def portable_definition(definition: dict) -> dict:
    payload = dict(definition or {})
    for field in ("id", "slug", "version"):
        payload.pop(field, None)
    for node in payload.get("nodes", []):
        node.pop("id", None)
    for connection in payload.get("connections", []):
        connection.pop("id", None)
    for trigger in payload.get("triggers", []):
        trigger.pop("id", None)
    return payload


@transaction.atomic
def save_workflow_definition(workflow: Workflow, definition: dict, user=None) -> Workflow:
    result = validate_definition(definition)
    if not result["valid"]:
        raise WorkflowValidationError(result["errors"])

    editable = ("name", "description", "status")
    for field in editable:
        if field in definition and definition[field] is not None:
            setattr(workflow, field, definition[field])

    node_payload = definition.get("nodes", [])
    node_map = {}
    desired_keys = set()
    for payload in node_payload:
        key = str(payload["key"])
        desired_keys.add(key)
        position = payload.get("position") or {}
        node, _ = WorkflowNode.objects.update_or_create(
            workflow=workflow,
            key=key,
            defaults={
                "name": payload.get("name") or key,
                "type": payload["type"],
                "config": payload.get("config") or {},
                "position_x": int(position.get("x", 0) or 0),
                "position_y": int(position.get("y", 0) or 0),
            },
        )
        node_map[key] = node
    workflow.nodes.exclude(key__in=desired_keys).delete()

    desired_connections = set()
    for payload in definition.get("connections", []):
        source = node_map.get(str(payload.get("source")))
        target = node_map.get(str(payload.get("target")))
        if not source or not target:
            continue
        connection, _ = WorkflowConnection.objects.update_or_create(
            workflow=workflow,
            source=source,
            target=target,
            source_handle=payload.get("source_handle") or "out",
            defaults={
                "target_handle": payload.get("target_handle") or "in",
                "label": payload.get("label") or "",
            },
        )
        desired_connections.add(connection.pk)
    workflow.connections.exclude(pk__in=desired_connections).delete()

    if "triggers" in definition:
        desired_triggers = set()
        for payload in definition["triggers"]:
            trigger_id = payload.get("id")
            trigger = workflow.triggers.filter(pk=trigger_id).first() if trigger_id else None
            if trigger is None:
                trigger = WorkflowTrigger(
                    workflow=workflow,
                    name=payload.get("name") or f"{payload.get('type', 'manual').title()} trigger",
                    type=payload.get("type") or "manual",
                )
            trigger.name = payload.get("name") or trigger.name
            trigger.type = payload.get("type") or trigger.type
            trigger.config = payload.get("config") or {}
            trigger.is_active = bool(payload.get("is_active", True))
            trigger.save()
            desired_triggers.add(trigger.pk)
        workflow.triggers.exclude(pk__in=desired_triggers).delete()

    workflow.definition = definition
    workflow.version = F("version") + 1
    workflow.save()
    workflow.refresh_from_db()

    WorkflowVersion.objects.create(
        workflow=workflow,
        version=workflow.version,
        definition=portable_definition(workflow_definition(workflow)),
        created_by=user,
    )
    log_action(user, "workflow.updated", workflow, {"version": workflow.version, "validation": result}, workflow.organization)
    return workflow


def cancel_execution(execution: WorkflowExecution, user=None) -> WorkflowExecution:
    if execution.status not in {"queued", "running"}:
        raise ValueError("Only queued or running executions can be cancelled.")
    if execution.celery_task_id:
        try:
            current_app.control.revoke(execution.celery_task_id, terminate=True)
        except Exception:
            pass
    execution.status = "cancelled"
    execution.error = "Cancelled by user."
    execution.finished_at = timezone.now()
    execution.save(update_fields=["status", "error", "finished_at"])
    log_action(
        user,
        "workflow.execution_cancelled",
        execution.workflow,
        {"execution_id": execution.pk},
        execution.workflow.organization,
    )
    return execution


def retry_execution(execution: WorkflowExecution, user=None) -> WorkflowExecution:
    if execution.status not in {"failed", "cancelled", "timed_out"}:
        raise ValueError("Only failed, cancelled or timed out executions can be retried.")
    enforce_limit(execution.workflow.organization, "executions_per_month")
    new_execution = execute_workflow_async(
        execution.workflow,
        input_data=execution.input,
        triggered_by=user,
        trigger=execution.trigger,
    )
    log_action(
        user,
        "workflow.execution_retried",
        execution.workflow,
        {"execution_id": execution.pk, "new_execution_id": new_execution.pk},
        execution.workflow.organization,
    )
    return new_execution


@transaction.atomic
def restore_workflow_version(version: WorkflowVersion, user=None) -> Workflow:
    workflow = save_workflow_definition(workflow=version.workflow, definition=version.definition, user=user)
    log_action(
        user,
        "workflow.restored",
        workflow,
        {"version_id": version.pk, "restored_from": version.version, "new_version": workflow.version},
        workflow.organization,
    )
    return workflow


def execute_workflow_async(
    workflow: Workflow,
    input_data: dict | None = None,
    triggered_by=None,
    trigger: WorkflowTrigger | None = None,
) -> WorkflowExecution:
    from .engine import execute_workflow
    from .tasks import execute_workflow_task

    execution = WorkflowExecution.objects.create(
        workflow=workflow,
        trigger=trigger,
        trigger_type=trigger.type if trigger else "manual",
        status="queued",
        input=input_data or {},
        triggered_by=triggered_by,
    )
    try:
        result = execute_workflow_task.delay(execution.pk)
        execution.celery_task_id = str(result.id)
        execution.save(update_fields=["celery_task_id"])
    except Exception:
        execute_workflow(workflow, input_data=input_data, triggered_by=triggered_by, trigger=trigger, execution=execution)
    return execution


@transaction.atomic
def create_workflow_from_definition(
    organization,
    definition: dict,
    user=None,
    name: str | None = None,
    as_template: bool = False,
) -> Workflow:
    payload = portable_definition(definition)
    payload["name"] = name or payload.get("name") or "Imported workflow"
    if as_template:
        payload["status"] = "draft"
    workflow = Workflow.objects.create(
        organization=organization,
        workspace=organization.workspaces.first(),
        name=payload["name"],
        description=payload.get("description", ""),
        status=payload.get("status", "draft"),
        is_template=as_template,
        created_by=user,
    )
    return save_workflow_definition(workflow, payload, user)


@transaction.atomic
def create_workflow_from_template(
    template: Workflow, organization, user=None, name: str | None = None
) -> Workflow:
    definition = workflow_definition(template)
    definition["status"] = "draft"
    return create_workflow_from_definition(
        organization,
        definition,
        user=user,
        name=name or f"{template.name} copy",
        as_template=False,
    )


@transaction.atomic
def duplicate_workflow(workflow: Workflow, user=None, name: str | None = None) -> Workflow:
    definition = workflow_definition(workflow)
    definition.pop("id", None)
    definition.pop("slug", None)
    definition.pop("version", None)
    definition["name"] = name or f"Copy of {workflow.name}"
    definition["status"] = "draft"
    for node in definition["nodes"]:
        node.pop("id", None)
    for connection in definition["connections"]:
        connection.pop("id", None)
    for trigger in definition["triggers"]:
        trigger.pop("id", None)

    clone = Workflow.objects.create(
        organization=workflow.organization,
        workspace=workflow.workspace,
        name=definition["name"],
        description=definition["description"],
        status="draft",
        created_by=user,
    )
    save_workflow_definition(clone, definition, user)
    return clone
