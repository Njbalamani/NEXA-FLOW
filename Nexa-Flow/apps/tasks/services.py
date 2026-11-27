from __future__ import annotations

from apps.audit.services import log_action
from apps.notifications.services import notify

from .models import Task, TaskEvent


def create_task(organization, data: dict, user=None) -> Task:
    task = Task.objects.create(organization=organization, created_by=user, **data)
    TaskEvent.objects.create(task=task, actor=user, event="created", data={"title": task.title})
    log_action(user, "task.created", task, {"title": task.title}, organization)
    if task.assignee:
        notify(organization, task.assignee, "Task assigned", task.title, "task_assigned", {"task_id": task.pk})
    return task


def change_task_status(task: Task, status: str, user=None) -> Task:
    previous = task.status
    task.status = status
    task.save(update_fields=["status", "completed_at", "updated_at"])
    TaskEvent.objects.create(task=task, actor=user, event="status_changed", data={"from": previous, "to": status})
    log_action(user, "task.status_changed", task, {"from": previous, "to": status}, task.organization)
    if task.assignee and status == "completed":
        notify(task.organization, task.assignee, "Task completed", task.title, "task_completed", {"task_id": task.pk})
    return task
