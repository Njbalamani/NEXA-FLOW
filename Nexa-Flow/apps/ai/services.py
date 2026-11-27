from __future__ import annotations

import json
import re

import requests
from django.conf import settings
from django.db.models import Count, Q
from django.utils import timezone

from apps.workflows.validation import validate_definition

OPERATOR_MAP = {
    ">": "gt",
    ">=": "gte",
    "<": "lt",
    "<=": "lte",
    "=": "eq",
    "==": "eq",
    "!=": "ne",
}


def _ai_call(system: str, prompt: str) -> str | None:
    api_key = settings.NEXAFLOW["AI_API_KEY"]
    if not api_key:
        return None
    try:
        response = requests.post(
            settings.NEXAFLOW["AI_API_URL"],
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": settings.NEXAFLOW["AI_MODEL"],
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt[:8000]},
                ],
                "temperature": 0.2,
                "max_tokens": 1200,
            },
            timeout=settings.NEXAFLOW["AI_TIMEOUT_SECONDS"],
        )
        response.raise_for_status()
        payload = response.json()
        return payload["choices"][0]["message"]["content"].strip()
    except Exception:
        return None


def _parse_trigger(text: str) -> dict:
    interval = re.search(r"every\s+(\d+)\s+(minute|hour|day)s?", text)
    daily = re.search(r"daily\s+at\s+(\d{1,2}):(\d{2})", text)
    if daily:
        return {
            "type": "schedule",
            "name": "Daily schedule",
            "config": {"mode": "cron", "cron": f"{int(daily.group(2))} {int(daily.group(1))} * * *"},
        }
    if interval:
        amount = int(interval.group(1))
        unit = interval.group(2)
        minutes = amount * {"minute": 1, "hour": 60, "day": 1440}[unit]
        return {"type": "schedule", "name": f"Every {amount} {unit}(s)", "config": {"mode": "interval", "minutes": minutes}}
    if "webhook" in text or "incoming request" in text:
        return {"type": "webhook", "name": "Incoming webhook", "config": {"event": "received"}}
    return {"type": "manual", "name": "Manual trigger", "config": {}}


def _parse_condition(text: str) -> dict | None:
    match = re.search(
        r"if\s+([a-zA-Z_][\w.]*)\s*(>=|<=|==|!=|>|<|=)\s*([-\d.]+|\"[^\"]+\"|'[^']+'|[a-zA-Z_]+)",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    field, operator, raw_value = match.groups()
    value: object = raw_value.strip("'\"")
    if re.fullmatch(r"-?\d+(\.\d+)?", str(value)):
        value = float(value) if "." in str(value) else int(value)
    return {
        "field": f"trigger.payload.{field}",
        "operator": OPERATOR_MAP[operator],
        "value": value,
    }


def _parse_actions(text: str) -> list[dict]:
    actions = []
    if re.search(r"\b(email|e-mail|mail)\b", text):
        actions.append(
            {
                "key": f"action_{len(actions) + 1}",
                "type": "action",
                "name": "Send email",
                "config": {
                    "action": "send_email",
                    "to": "{{trigger.payload.email}}",
                    "subject": "NexaFlow: {{workflow.name}}",
                    "body": text[:500],
                    "retry_attempts": 3,
                    "retry_delay_seconds": 2,
                },
            }
        )
    if re.search(r"\b(task|assign|follow up)\b", text):
        actions.append(
            {
                "key": f"action_{len(actions) + 1}",
                "type": "action",
                "name": "Create task",
                "config": {
                    "action": "create_task",
                    "title": "Follow up: {{workflow.name}}",
                    "description": text[:500],
                    "assignee_email": "{{trigger.payload.email}}",
                    "priority": "medium",
                    "due_in_hours": 24,
                },
            }
        )
    if re.search(r"\bslack\b", text):
        actions.append(
            {
                "key": f"action_{len(actions) + 1}",
                "type": "action",
                "name": "Notify Slack",
                "config": {"action": "slack", "message": "NexaFlow: " + text[:400]},
            }
        )
    if re.search(r"\b(webhook|post to|http|api)\b", text):
        actions.append(
            {
                "key": f"action_{len(actions) + 1}",
                "type": "action",
                "name": "Call REST API",
                "config": {
                    "action": "http_request",
                    "url": "https://hooks.example.invalid/nexaflow",
                    "method": "POST",
                    "body": {"message": text[:400]},
                    "timeout": 10,
                    "retry_attempts": 2,
                },
            }
        )
    if not actions:
        actions.append(
            {
                "key": "action_1",
                "type": "action",
                "name": "Log result",
                "config": {"action": "log", "message": text[:500]},
            }
        )
    for index, action in enumerate(actions):
        action["position"] = {"x": 420 + index * 260, "y": 160}
    return actions


def generate_workflow(description: str) -> dict:
    text = (description or "").strip()
    if len(text) < 10:
        raise ValueError("Describe the workflow in at least 10 characters.")

    ai_text = _ai_call(
        "You design safe NexaFlow workflow graphs. Return JSON only with nodes, connections and triggers. "
        "Allowed node types are trigger, condition, action, delay, transform and webhook.",
        f"Convert this business process into a NexaFlow workflow graph: {text}",
    )
    if ai_text:
        try:
            parsed = json.loads(ai_text.strip("`").replace("json", "", 1))
            if isinstance(parsed, dict) and "nodes" in parsed:
                validation = validate_definition(parsed)
                if validation["valid"]:
                    return {
                        "source": "ai",
                        "definition": parsed,
                        "validation": validation,
                        "explanation": "AI-generated workflow validated successfully.",
                        "confidence": 0.86,
                    }
        except (ValueError, TypeError):
            pass

    trigger = _parse_trigger(text)
    condition = _parse_condition(text)
    actions = _parse_actions(text)
    nodes = [
        {
            "key": "trigger_1",
            "type": "trigger",
            "name": trigger["name"],
            "config": {},
            "position": {"x": 60, "y": 160},
        }
    ]
    connections = []
    previous = "trigger_1"

    if condition:
        nodes.append(
            {
                "key": "condition_1",
                "type": "condition",
                "name": "Check condition",
                "config": condition,
                "position": {"x": 240, "y": 160},
            }
        )
        connections.append({"source": previous, "target": "condition_1", "source_handle": "out"})
        previous = "condition_1"

    for action in actions:
        nodes.append(action)
        handle = "true" if previous == "condition_1" else "out"
        connections.append({"source": previous, "target": action["key"], "source_handle": handle})
        previous = action["key"]

    definition = {
        "name": re.sub(r"[^a-zA-Z0-9 ]+", "", text[:60]).strip().title() or "AI Suggested Workflow",
        "description": text,
        "status": "draft",
        "nodes": nodes,
        "connections": connections,
        "triggers": [trigger],
    }
    validation = validate_definition(definition)
    return {
        "source": "local-assistant",
        "definition": definition,
        "validation": validation,
        "explanation": (
            "Generated a workflow from the description. "
            f"Trigger: {trigger['name']}. Actions: {', '.join(action['name'] for action in actions)}."
        ),
        "confidence": 0.72,
    }


def explain_failure(execution) -> str:
    failed_steps = execution.steps.filter(status="failed")
    step_text = "; ".join(f"{step.node_name}: {step.error}" for step in failed_steps) or "No failed step was recorded."
    prompt = f"Workflow: {execution.workflow.name}\nError: {execution.error}\nSteps: {step_text}"
    remote = _ai_call("Explain this workflow failure concisely and suggest one fix.", prompt)
    if remote:
        return remote
    return (
        f"Execution #{execution.pk} failed. {execution.error or 'Unknown error'} "
        f"Failed steps: {step_text} Check the node configuration and retry after correcting the input."
    )


def suggest_improvements(workflow) -> list[str]:
    from apps.workflows.validation import validate_workflow

    validation = validate_workflow(workflow)
    suggestions = list(validation["warnings"])
    if not workflow.nodes.filter(type="condition").exists():
        suggestions.append("Add a condition node to route unexpected payloads before running actions.")
    if not workflow.triggers.filter(is_active=True).exists():
        suggestions.append("Enable at least one trigger so the workflow can run automatically.")
    failed = workflow.executions.filter(status="failed").count()
    if failed:
        suggestions.append(f"{failed} failed execution(s) recorded; add retry handling to external actions.")
    if not suggestions:
        suggestions.append("Workflow structure looks healthy. Add a notification action for exception monitoring.")
    remote = _ai_call(
        "Suggest up to three concrete workflow improvements. Return a plain text list.",
        f"Workflow {workflow.name}: {suggestions}",
    )
    if remote:
        suggestions.insert(0, remote[:1200])
    return suggestions


def recommend_automations(organization) -> list[dict]:
    recommendations = []
    workflows = organization.workflows.all()
    if workflows.filter(status="active", nodes__type__isnull=True).exists():
        recommendations.append(
            {
                "title": "Complete empty workflows",
                "reason": "Some active workflows have no nodes and cannot execute.",
                "impact": "high",
            }
        )
    manual_count = workflows.filter(triggers__type="manual").count()
    if manual_count >= 3:
        recommendations.append(
            {
                "title": "Schedule repetitive manual workflows",
                "reason": f"{manual_count} workflows depend on manual triggers.",
                "impact": "medium",
            }
        )
    overdue = organization.tasks.filter(due_at__lt=timezone.now(), status="pending").count()
    if overdue:
        recommendations.append(
            {
                "title": "Automate overdue task follow-up",
                "reason": f"{overdue} task(s) are overdue and pending.",
                "impact": "high",
            }
        )
    failed_count = organization.workflows.filter(executions__status="failed").aggregate(total=Count("executions"))["total"]
    if failed_count:
        recommendations.append(
            {
                "title": "Add failure notifications",
                "reason": f"{failed_count} failed execution(s) need operator attention.",
                "impact": "high",
            }
        )
    return recommendations


def explain_text(prompt: str) -> str:
    remote = _ai_call("Summarize the request in one sentence.", prompt)
    return remote or f"NexaFlow analysis: {str(prompt)[:600]}"
