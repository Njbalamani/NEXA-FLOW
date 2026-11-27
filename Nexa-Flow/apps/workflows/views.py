import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from apps.billing.limits import PlanLimitExceeded, enforce_limit
from apps.core.decorators import organization_required, role_required
from apps.core.permissions import get_organization
from apps.workflows.services import cancel_execution, execute_workflow_async, retry_execution, workflow_definition

from .models import Workflow, WorkflowExecution
from .validation import validate_workflow


@login_required
@organization_required
def workflow_list(request):
    organization = get_organization(request)
    queryset = Workflow.objects.filter(organization=organization)
    search = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    if search:
        queryset = queryset.filter(Q(name__icontains=search) | Q(description__icontains=search))
    if status:
        queryset = queryset.filter(status=status)
    return render(
        request,
        "workflows/list.html",
        {"workflows": queryset, "search": search, "status": status, "status_choices": Workflow.STATUS_CHOICES},
    )


@login_required
@role_required("manager")
def workflow_create(request):
    organization = get_organization(request)
    try:
        enforce_limit(organization, "workflows")
    except PlanLimitExceeded as exc:
        messages.error(request, str(exc))
        return redirect("workflow-list")
    if request.method != "POST":
        return redirect("workflow-list")
    name = request.POST.get("name", "Untitled workflow").strip() or "Untitled workflow"
    workflow = Workflow.objects.create(
        organization=organization,
        workspace=organization.workspaces.first(),
        name=name,
        description=request.POST.get("description", ""),
        created_by=request.user,
    )
    messages.success(request, "Workflow created. Design its logic on the canvas.")
    return redirect("workflow-builder", workflow_id=workflow.pk)


@login_required
@organization_required
def workflow_builder(request, workflow_id):
    organization = get_organization(request)
    workflow = get_object_or_404(Workflow, pk=workflow_id, organization=organization)
    definition = workflow_definition(workflow)
    validation = validate_workflow(workflow)
    integrations = [
        {"id": item.pk, "name": item.name, "provider": item.provider}
        for item in organization.integrations.filter(is_active=True)
    ]
    nodes_3d = json.dumps(
        [
            {"type": node.type, "position": {"x": node.position_x, "y": node.position_y}}
            for node in workflow.nodes.all()
        ]
    )
    return render(
        request,
        "workflows/builder.html",
        {
            "workflow": workflow,
            "definition_json": json.dumps(definition),
            "nodes_3d_json": nodes_3d,
            "validation": validation,
            "integrations": integrations,
            "recent_executions": workflow.executions.select_related("trigger")[:8],
        },
    )


@login_required
@role_required("manager")
def workflow_delete(request, workflow_id):
    organization = get_organization(request)
    workflow = get_object_or_404(Workflow, pk=workflow_id, organization=organization)
    if request.method == "POST":
        workflow.delete()
        messages.success(request, "Workflow deleted.")
    return redirect("workflow-list")


@login_required
@role_required("manager")
def workflow_execute(request, workflow_id):
    organization = get_organization(request)
    workflow = get_object_or_404(Workflow, pk=workflow_id, organization=organization)
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    validation = validate_workflow(workflow)
    if not validation["valid"]:
        messages.error(request, "Fix workflow validation errors before executing.")
        return redirect("workflow-builder", workflow_id=workflow.pk)
    try:
        enforce_limit(organization, "executions_per_month")
    except PlanLimitExceeded as exc:
        messages.error(request, str(exc))
        return redirect("workflow-builder", workflow_id=workflow.pk)
    execution = execute_workflow_async(workflow, input_data={"triggered_from": "ui"}, triggered_by=request.user)
    messages.success(request, f"Execution #{execution.pk} queued.")
    return redirect("workflow-builder", workflow_id=workflow.pk)


@login_required
@organization_required
def execution_list(request):
    organization = get_organization(request)
    executions = WorkflowExecution.objects.filter(workflow__organization=organization).select_related(
        "workflow", "trigger", "triggered_by"
    )
    return render(request, "workflows/executions.html", {"executions": executions})


@login_required
@organization_required
def execution_detail(request, execution_id):
    organization = get_organization(request)
    execution = get_object_or_404(
        WorkflowExecution.objects.select_related("workflow").prefetch_related("steps"),
        pk=execution_id,
        workflow__organization=organization,
    )
    return render(request, "workflows/execution_detail.html", {"execution": execution})


@login_required
@role_required("manager")
def execution_cancel(request, execution_id):
    organization = get_organization(request)
    execution = get_object_or_404(WorkflowExecution, pk=execution_id, workflow__organization=organization)
    if request.method == "POST":
        try:
            cancel_execution(execution, request.user)
            messages.success(request, f"Execution #{execution.pk} cancelled.")
        except ValueError as exc:
            messages.error(request, str(exc))
    return redirect("execution-detail", execution_id=execution.pk)


@login_required
@role_required("manager")
def execution_retry(request, execution_id):
    organization = get_organization(request)
    execution = get_object_or_404(
        WorkflowExecution.objects.select_related("workflow", "trigger"),
        pk=execution_id,
        workflow__organization=organization,
    )
    if request.method == "POST":
        try:
            new_execution = retry_execution(execution, request.user)
            messages.success(request, f"Execution #{new_execution.pk} queued.")
            return redirect("execution-detail", execution_id=new_execution.pk)
        except (ValueError, PlanLimitExceeded) as exc:
            messages.error(request, str(exc))
    return redirect("execution-detail", execution_id=execution.pk)
