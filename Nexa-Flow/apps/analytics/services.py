from __future__ import annotations

import json
from datetime import timedelta

from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from django.utils import timezone

from apps.tasks.models import Task
from apps.workflows.models import Workflow, WorkflowExecution


def dashboard_metrics(organization) -> dict:
    now = timezone.now()
    start = (now - timedelta(days=13)).date()

    workflows = Workflow.objects.filter(organization=organization)
    executions = WorkflowExecution.objects.filter(workflow__organization=organization)
    tasks = Task.objects.filter(organization=organization)

    total_executions = executions.count()
    success_count = executions.filter(status="success").count()
    failed_count = executions.filter(status="failed").count()
    daily_rows = (
        executions.filter(created_at__date__gte=start)
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(total=Count("id"), success=Count("id", filter=Q(status="success")), failed=Count("id", filter=Q(status="failed")))
        .order_by("day")
    )
    daily_map = {row["day"]: row for row in daily_rows}
    labels = []
    success_series = []
    failed_series = []
    for offset in range(14):
        day = start + timedelta(days=offset)
        labels.append(day.strftime("%b %d"))
        row = daily_map.get(day)
        success_series.append(row["success"] if row else 0)
        failed_series.append(row["failed"] if row else 0)

    status_counts = dict(workflows.values_list("status").annotate(count=Count("id")))
    task_counts = dict(tasks.values_list("status").annotate(count=Count("id")))

    return {
        "total_workflows": workflows.count(),
        "active_workflows": workflows.filter(status="active").count(),
        "draft_workflows": status_counts.get("draft", 0),
        "completed_executions": success_count,
        "failed_executions": failed_count,
        "running_executions": executions.filter(status__in={"queued", "running"}).count(),
        "pending_tasks": tasks.filter(status="pending").count(),
        "overdue_tasks": tasks.filter(due_at__lt=now, status__in={"pending", "in_progress"}).count(),
        "success_rate": round((success_count / total_executions * 100) if total_executions else 0, 1),
        "recent_activity": executions.select_related("workflow", "triggered_by").order_by("-created_at")[:12],
        "top_workflows": workflows.annotate(runs=Count("executions")).order_by("-runs")[:6],
        "chart_data": json.dumps({"labels": labels, "success": success_series, "failed": failed_series}),
        "workflow_status_data": json.dumps(
            {
                "labels": ["Active", "Draft", "Paused", "Archived"],
                "values": [
                    status_counts.get("active", 0),
                    status_counts.get("draft", 0),
                    status_counts.get("paused", 0),
                    status_counts.get("archived", 0),
                ],
            }
        ),
        "task_status_data": json.dumps(
            {
                "labels": ["Pending", "In progress", "Completed", "Cancelled"],
                "values": [
                    task_counts.get("pending", 0),
                    task_counts.get("in_progress", 0),
                    task_counts.get("completed", 0),
                    task_counts.get("cancelled", 0),
                ],
            }
        ),
    }
