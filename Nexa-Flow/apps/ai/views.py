import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from apps.core.decorators import organization_required
from apps.core.permissions import get_organization
from apps.workflows.models import Workflow, WorkflowExecution
from apps.workflows.services import save_workflow_definition
from apps.workflows.validation import WorkflowValidationError

from .services import explain_failure, generate_workflow, recommend_automations, suggest_improvements


@login_required
@organization_required
def assistant_view(request):
    organization = get_organization(request)
    result = None
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "generate":
            try:
                result = generate_workflow(request.POST.get("description", ""))
                if not result["validation"]["valid"]:
                    messages.error(request, "Generated workflow did not pass validation.")
            except ValueError as exc:
                messages.error(request, str(exc))
        elif action == "save":
            try:
                definition = json.loads(request.POST.get("definition_json") or "{}")
                workflow = Workflow.objects.create(
                    organization=organization,
                    workspace=organization.workspaces.first(),
                    name=definition.get("name") or "AI workflow",
                    description=definition.get("description", ""),
                    status="draft",
                    created_by=request.user,
                )
                save_workflow_definition(workflow, definition, request.user)
                messages.success(request, "AI-generated workflow saved as a draft.")
                return redirect("workflow-builder", workflow_id=workflow.pk)
            except (ValueError, WorkflowValidationError) as exc:
                messages.error(request, f"Could not save workflow: {exc}")
        elif action == "explain":
            execution = WorkflowExecution.objects.filter(pk=request.POST.get("execution_id"), workflow__organization=organization).first()
            result = {"explanation": explain_failure(execution) if execution else "Execution not found."}

    workflow_ideas = [
        {
            "workflow": workflow,
            "suggestions": suggest_improvements(workflow),
        }
        for workflow in organization.workflows.all()[:5]
    ]
    return render(
        request,
        "ai/assistant.html",
        {
            "result": result,
            "result_json": json.dumps(result["definition"]) if result and "definition" in result else "",
            "recommendations": recommend_automations(organization),
            "workflow_ideas": workflow_ideas,
            "executions": WorkflowExecution.objects.filter(workflow__organization=organization, status="failed")[:10],
        },
    )
