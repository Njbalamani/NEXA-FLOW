from __future__ import annotations

from datetime import timedelta

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from .models import WorkflowExecution, WorkflowTrigger
from .services import execute_workflow_async


def _field_matches(pattern: str, value: int, low: int, high: int) -> bool:
    for part in str(pattern).strip().split(","):
        part = part.strip()
        if not part:
            continue
        base, _, step_text = part.partition("/")
        try:
            step = int(step_text) if step_text else 1
        except ValueError:
            continue
        if step < 1:
            continue
        if base in {"*", "?"}:
            start, end = low, high
        elif "-" in base:
            try:
                start, end = (int(piece) for piece in base.split("-", 2)[:2])
            except ValueError:
                continue
        elif base.isdigit():
            start = end = int(base)
        else:
            continue
        if not (low <= start <= high and low <= end <= high and start <= end):
            continue
        if start <= value <= end and (value - start) % step == 0:
            return True
    return False


def is_schedule_due(trigger: WorkflowTrigger, now=None) -> bool:
    now = now or timezone.now()
    config = trigger.config or {}
    if config.get("mode", "interval") == "interval":
        try:
            minutes = max(1, int(config.get("minutes", 60)))
        except (TypeError, ValueError):
            return False
        if not trigger.last_run_at:
            return True
        return now >= trigger.last_run_at + timedelta(minutes=minutes)

    cron = str(config.get("cron", "")).split()
    if len(cron) != 5:
        return False
    minute, hour, day, month, weekday = cron
    values = [now.minute, now.hour, now.day, now.month, (now.weekday() + 1) % 7]
    ranges = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]
    return all(
        _field_matches(pattern, value, low, high)
        for pattern, value, (low, high) in zip(cron, values, ranges)
    )


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_jitter=True, max_retries=3)
def execute_workflow_task(self, execution_id: int) -> int:
    from .engine import run_execution

    execution = WorkflowExecution.objects.select_related("workflow", "trigger", "workflow__organization").get(pk=execution_id)
    if execution.status == "cancelled":
        return execution.pk
    execution.attempts = int(self.request.retries or 0) + 1
    execution.celery_task_id = str(self.request.id)
    execution.save(update_fields=["attempts", "celery_task_id"])
    run_execution(execution, resume=bool(self.request.retries))
    return execution.pk


@shared_task
def run_due_workflows() -> dict:
    now = timezone.now()
    count = 0
    triggers = WorkflowTrigger.objects.filter(
        type="schedule", is_active=True, workflow__status="active"
    ).select_related("workflow")
    for trigger in triggers:
        if is_schedule_due(trigger, now):
            with transaction.atomic():
                updated = WorkflowTrigger.objects.filter(pk=trigger.pk, last_run_at=trigger.last_run_at).update(
                    last_run_at=now
                )
            if not updated:
                continue
            try:
                from apps.billing.limits import enforce_limit

                enforce_limit(trigger.workflow.organization, "executions_per_month")
            except Exception:
                continue
            execute_workflow_async(
                trigger.workflow,
                input_data={"scheduled_at": now.isoformat(), "trigger_id": trigger.pk},
                trigger=trigger,
            )
            count += 1
    return {"scheduled": count, "checked_at": now.isoformat()}
