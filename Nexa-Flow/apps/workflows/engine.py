from __future__ import annotations

import json
import time
from collections import defaultdict, deque
from datetime import timedelta

import requests
from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from apps.core.security import is_safe_outbound_url
from apps.workflows.expressions import evaluate_condition, render, safe_eval

from .models import ExecutionStep, Workflow, WorkflowExecution, WorkflowNode
from .validation import validate_definition

MAX_OUTPUT_CHARS = 4000


class ExecutionError(RuntimeError):
    pass


def _truncate(text: str) -> str:
    return text[:MAX_OUTPUT_CHARS]


def _publish(workflow_id: int, payload: dict):
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer

        layer = get_channel_layer()
        if layer:
            async_to_sync(layer.group_send)(
                f"workflow_{workflow_id}", {"type": "workflow.event", "event": payload}
            )
    except Exception:
        pass


def _integration_context(config: dict, organization) -> dict:
    integration_id = config.get("integration_id")
    if not integration_id:
        return {}
    from apps.integrations.models import Integration
    from apps.integrations.services import integration_credentials

    integration = Integration.objects.filter(pk=integration_id, organization=organization).first()
    if not integration or not integration.is_active:
        raise ExecutionError(f"Integration #{integration_id} is unavailable.")
    return integration_credentials(integration)


def _resolve_headers(config: dict, organization) -> dict:
    credentials = _integration_context(config, organization)
    headers = {}
    for key, value in (config.get("headers") or {}).items():
        value = str(render(value, {"vars": {}, "input": {}}))
        if value.startswith("env:"):
            import os

            name = value[4:]
            allowed = set(settings.NEXAFLOW.get("ALLOWED_ENV_HEADERS") or [])
            if name not in allowed:
                raise ExecutionError(f"Environment variable '{name}' is not allowed for workflow headers.")
            value = os.environ.get(name, "")
        elif value.startswith("credential:"):
            value = str(credentials.get(value.split(":", 1)[1], ""))
        headers[str(key)] = value
    token = credentials.get("token") or credentials.get("api_key")
    if token and config.get("use_integration_auth", True):
        headers.setdefault("Authorization", f"Bearer {token}")
    return headers


def _http_request(config: dict, context: dict, organization) -> dict:
    url = str(render(config.get("url") or "", context))
    if not is_safe_outbound_url(url):
        raise ExecutionError("Outbound URL is not allowed.")
    method = str(config.get("method") or "POST").upper()
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        raise ExecutionError(f"Unsupported HTTP method: {method}")
    headers = _resolve_headers(config, organization)
    body = render(config.get("body") or {}, context)
    timeout = max(1, min(int(config.get("timeout") or 10), 30))
    response = requests.request(
        method,
        url,
        headers=headers,
        json=body if method not in {"GET", "DELETE"} else None,
        params=body if method in {"GET", "DELETE"} else None,
        timeout=timeout,
        allow_redirects=False,
    )
    if response.status_code >= 400:
        raise ExecutionError(f"HTTP {response.status_code}: {_truncate(response.text)}")
    return {"status_code": response.status_code, "body": _truncate(response.text)}


def _execute_action(action: str, config: dict, context: dict, workflow: Workflow, triggered_by) -> dict:
    organization = workflow.organization
    if action in {"http_request", "webhook"}:
        return _http_request(config, context, organization)
    if action == "slack":
        credentials = _integration_context(config, organization)
        url = str(render(config.get("webhook_url") or credentials.get("webhook_url") or "", context))
        if not is_safe_outbound_url(url):
            raise ExecutionError("Slack webhook URL is not allowed.")
        result = _http_request(
            {
                "url": url,
                "method": "POST",
                "body": {"text": str(render(config.get("message") or "", context))},
                "timeout": 10,
                "use_integration_auth": False,
                "headers": {},
            },
            context,
            organization,
        )
        return {"delivered": True, "status_code": result["status_code"]}

    if action == "send_email":
        recipients = config.get("to") or ([triggered_by.email] if triggered_by else [])
        if isinstance(recipients, str):
            recipients = [address.strip() for address in recipients.split(",") if address.strip()]
        recipients = [str(render(address, context)) for address in recipients]
        if not recipients:
            raise ExecutionError("Email action requires a recipient.")
        subject = str(render(config.get("subject") or "NexaFlow notification", context))
        message = str(render(config.get("body") or "", context))
        send_mail(subject, message, settings.NEXAFLOW["EMAIL_FROM"], recipients, fail_silently=False)
        return {"sent_to": recipients, "subject": subject}

    if action == "create_task":
        from apps.tasks.models import Task

        assignee = None
        assignee_email = str(render(config.get("assignee_email") or "", context))
        if assignee_email:
            from apps.accounts.models import User

            assignee = User.objects.filter(
                email__iexact=assignee_email,
                organization_memberships__organization=organization,
                organization_memberships__is_active=True,
            ).first()
        due = None
        if config.get("due_in_hours"):
            due = timezone.now() + timedelta(hours=float(config["due_in_hours"]))
        task = Task.objects.create(
            organization=organization,
            workspace=workflow.workspace,
            workflow=workflow,
            title=str(render(config.get("title") or workflow.name, context)),
            description=str(render(config.get("description") or "", context)),
            assignee=assignee,
            created_by=triggered_by,
            priority=config.get("priority") or "medium",
            due_at=due,
            metadata={"source": "workflow", "workflow_id": workflow.pk},
        )
        return {"task_id": task.pk, "title": task.title}

    if action == "notification":
        from apps.notifications.services import notify

        email = str(render(config.get("recipient_email") or "", context))
        recipient = None
        if email:
            from apps.accounts.models import User

            recipient = User.objects.filter(
                email__iexact=email,
                organization_memberships__organization=organization,
                organization_memberships__is_active=True,
            ).first()
        recipient = recipient or triggered_by
        notification = notify(
            organization=organization,
            user=recipient,
            title=str(render(config.get("title") or "Workflow notification", context)),
            body=str(render(config.get("body") or "", context)),
            category=config.get("category") or "workflow",
        )
        return {"notification_id": notification.pk if notification else None}

    if action == "ai":
        from apps.ai.services import explain_text

        output = explain_text(str(render(config.get("prompt") or workflow.name, context)))
        return {"suggestion": _truncate(output)}

    if action == "log":
        return {"message": str(render(config.get("message") or "", context))}

    raise ExecutionError(f"Unsupported action: {action}")


def _run_node(node: WorkflowNode, resolved_config: dict, context: dict, workflow: Workflow, triggered_by) -> tuple[dict, str | None]:
    if node.type == "trigger":
        return {"triggered": True, "payload": context.get("input", {})}, None
    if node.type == "condition":
        result = bool(evaluate_condition(resolved_config, context))
        return {"result": result, "branch": "true" if result else "false"}, "true" if result else "false"
    if node.type == "delay":
        seconds = max(0, min(int(resolved_config.get("seconds") or 0), 300))
        if seconds:
            time.sleep(seconds)
        return {"waited_seconds": seconds}, None
    if node.type == "transform":
        variables = resolved_config.get("variables") or {}
        if not isinstance(variables, dict):
            raise ExecutionError("Transform variables must be an object.")
        context.setdefault("vars", {}).update(variables)
        return {"variables": variables}, None
    if node.type == "webhook":
        return _http_request(resolved_config, context, workflow.organization), None
    if node.type == "action":
        action = str(resolved_config.get("action") or "")
        attempts = max(1, min(int(resolved_config.get("retry_attempts") or 1), 5))
        delay = max(0, min(int(resolved_config.get("retry_delay_seconds") or 0), 5))
        last_error = None
        for attempt in range(1, attempts + 1):
            try:
                return _execute_action(action, resolved_config, context, workflow, triggered_by), None
            except Exception as exc:
                last_error = exc
                if attempt < attempts and delay:
                    time.sleep(delay)
        raise ExecutionError(f"Action '{action}' failed after {attempts} attempt(s): {last_error}")
    raise ExecutionError(f"Unsupported node type: {node.type}")


def _execution_cancelled(execution: WorkflowExecution) -> bool:
    return WorkflowExecution.objects.filter(pk=execution.pk, status="cancelled").exists()


def _finish_execution(execution: WorkflowExecution, status: str, error: str = "", result: dict | None = None):
    execution.status = status
    execution.error = _truncate(error)
    execution.finished_at = timezone.now()
    if execution.started_at:
        execution.duration_ms = int((execution.finished_at - execution.started_at).total_seconds() * 1000)
    execution.result = result or execution.result
    execution.context["vars"] = execution.context.get("vars", {})
    execution.save(
        update_fields=["status", "error", "finished_at", "duration_ms", "result", "context"]
    )
    Workflow.objects.filter(pk=execution.workflow_id).update(last_executed_at=execution.finished_at)

    from apps.audit.services import log_action
    from apps.notifications.services import notify

    log_action(
        execution.triggered_by,
        "workflow.executed",
        execution.workflow,
        {"execution_id": execution.pk, "status": status, "error": error},
        execution.workflow.organization,
    )
    if status == "failed":
        notify(
            organization=execution.workflow.organization,
            user=execution.triggered_by,
            title=f"Execution failed: {execution.workflow.name}",
            body=error or "An execution failed.",
            category="execution_failed",
            data={"execution_id": execution.pk, "workflow_id": execution.workflow_id},
        )
    _publish(execution.workflow_id, {"type": "execution.updated", "execution_id": execution.pk, "status": status})


def run_execution(execution: WorkflowExecution, resume: bool = False) -> WorkflowExecution:
    workflow = execution.workflow
    if execution.status == "cancelled" or _execution_cancelled(execution):
        execution.refresh_from_db()
        return execution
    execution.status = "running"
    execution.started_at = timezone.now()
    execution.save(update_fields=["status", "started_at"])

    input_data = execution.input or {}
    trigger = execution.trigger
    context = {
        "input": input_data,
        "trigger": {
            "type": trigger.type if trigger else execution.trigger_type,
            "name": trigger.name if trigger else "Manual",
            "payload": input_data,
        },
        "workflow": {"id": workflow.pk, "name": workflow.name, "version": workflow.version},
        "organization": {"id": workflow.organization_id, "name": workflow.organization.name},
        "vars": {},
        "steps": {},
    }

    definition = {
        "nodes": [
            {
                "key": node.key,
                "type": node.type,
                "name": node.name,
                "config": node.config,
                "position": {"x": node.position_x, "y": node.position_y},
            }
            for node in workflow.nodes.all()
        ],
        "connections": [
            {
                "source": item.source.key,
                "target": item.target.key,
                "source_handle": item.source_handle,
            }
            for item in workflow.connections.select_related("source", "target")
        ],
        "triggers": [
            {"type": item.type, "name": item.name, "config": item.config, "is_active": item.is_active}
            for item in workflow.triggers.all()
        ],
    }
    validation = validate_definition(definition)
    if not validation["valid"]:
        _finish_execution(execution, "failed", "Invalid workflow: " + "; ".join(validation["errors"]))
        return execution

    nodes = {node.key: node for node in workflow.nodes.all()}
    outgoing = defaultdict(list)
    for connection in workflow.connections.select_related("source", "target"):
        outgoing[connection.source.key].append(
            (connection.source_handle, connection.target.key)
        )

    starts = validation["start_keys"]
    queue = deque((key, None) for key in starts)
    visited = set()
    step_results = {}
    error_message = ""

    if resume:
        for step in execution.steps.filter(status="success"):
            output = step.output or {}
            step_results[step.node_key] = output
            context["steps"][step.node_key] = output
            if isinstance(output.get("vars"), dict):
                context.setdefault("vars", {}).update(output["vars"])

    def enqueue_next(key: str, node: WorkflowNode, branch: str | None):
        if node.type == "condition":
            selected = branch or "out"
            next_keys = [target for handle, target in outgoing[key] if handle in {selected, "out"}]
        else:
            next_keys = [target for _, target in outgoing[key]]
        for target in next_keys:
            if target not in visited:
                queue.append((target, branch if node.type == "condition" else None))

    while queue:
        key, incoming_handle = queue.popleft()
        if key in visited or key not in nodes:
            continue
        visited.add(key)
        node = nodes[key]

        if _execution_cancelled(execution):
            execution.context = context
            execution.save(update_fields=["context"])
            execution.refresh_from_db()
            _publish(execution.workflow_id, {"type": "execution.cancelled", "execution_id": execution.pk})
            return execution

        if resume and key in step_results:
            output = step_results[key]
            branch = output.get("branch") if node.type == "condition" else None
            enqueue_next(key, node, branch)
            continue

        step, _ = ExecutionStep.objects.update_or_create(
            execution=execution,
            node_key=node.key,
            defaults={
                "node": node,
                "node_name": node.name,
                "node_type": node.type,
                "status": "running",
                "input": render(node.config, context),
                "output": {},
                "error": "",
                "finished_at": None,
                "attempt": execution.attempts or 1,
            },
        )
        try:
            resolved_config = render(node.config, context)
            output, branch = _run_node(node, resolved_config, context, workflow, execution.triggered_by)
            if isinstance(output.get("vars"), dict):
                context.setdefault("vars", {}).update(output["vars"])
            context["steps"][key] = output
            step_results[key] = output
            step.output = output
            step.status = "success"
            step.finished_at = timezone.now()
            step.save(update_fields=["output", "status", "finished_at"])

            enqueue_next(key, node, branch)
        except Exception as exc:
            error_message = f"Node '{node.key}' failed: {exc}"
            step.status = "failed"
            step.error = _truncate(error_message)
            step.finished_at = timezone.now()
            step.save(update_fields=["status", "error", "finished_at"])
            break

    execution.context = context
    if error_message:
        _finish_execution(execution, "failed", error_message, {"steps": step_results})
    else:
        _finish_execution(execution, "success", "", {"steps": step_results})
    return execution


def execute_workflow(
    workflow: Workflow,
    input_data: dict | None = None,
    triggered_by=None,
    trigger=None,
    execution: WorkflowExecution | None = None,
) -> WorkflowExecution:
    if execution is None:
        execution = WorkflowExecution.objects.create(
            workflow=workflow,
            trigger=trigger,
            trigger_type=trigger.type if trigger else "manual",
            status="queued",
            input=input_data or {},
            triggered_by=triggered_by,
        )
    return run_execution(execution)
