from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import redirect, render

from apps.core.decorators import organization_required
from apps.core.permissions import get_organization

from .models import Task
from .services import change_task_status, create_task


@login_required
@organization_required
def task_list(request):
    organization = get_organization(request)
    queryset = Task.objects.filter(organization=organization).select_related("assignee", "workflow")
    search = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    if search:
        queryset = queryset.filter(Q(title__icontains=search) | Q(description__icontains=search))
    if status:
        queryset = queryset.filter(status=status)
    return render(
        request,
        "tasks/list.html",
        {
            "tasks": queryset,
            "search": search,
            "status": status,
            "status_choices": Task.STATUS_CHOICES,
            "priority_choices": Task.PRIORITY_CHOICES,
            "members": organization.members.filter(is_active=True).select_related("user"),
        },
    )


@login_required
@organization_required
def task_create(request):
    organization = get_organization(request)
    if request.method == "POST":
        data = {
            "title": request.POST.get("title", "").strip(),
            "description": request.POST.get("description", ""),
            "assignee_id": request.POST.get("assignee") or None,
            "priority": request.POST.get("priority") or "medium",
            "due_at": request.POST.get("due_at") or None,
            "workspace": organization.workspaces.first(),
        }
        if data["assignee_id"] and not organization.members.filter(
            user_id=data["assignee_id"], is_active=True
        ).exists():
            messages.error(request, "Assignee must be an active member of this organization.")
            return redirect("task-list")
        if data["title"]:
            create_task(organization, data, request.user)
            messages.success(request, "Task created.")
        else:
            messages.error(request, "A task title is required.")
    return redirect("task-list")


@login_required
@organization_required
def task_status(request, task_id):
    organization = get_organization(request)
    task = Task.objects.filter(pk=task_id, organization=organization).first()
    if request.method == "POST" and task:
        status = request.POST.get("status")
        if status in dict(Task.STATUS_CHOICES):
            change_task_status(task, status, request.user)
            messages.success(request, "Task status updated.")
    return redirect("task-list")
