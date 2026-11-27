import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError
from django.shortcuts import get_object_or_404, redirect, render

from apps.billing.limits import PlanLimitExceeded, enforce_limit
from apps.core.decorators import organization_required, role_required
from apps.core.permissions import get_membership, get_organization, role_at_least

from .models import Integration
from .services import test_integration


@login_required
@organization_required
def integration_list(request):
    organization = get_organization(request)
    if request.method == "POST":
        membership = get_membership(request)
        if not membership or not role_at_least(membership.role, "admin"):
            messages.error(request, "Only organization admins can add integrations.")
            return redirect("integration-list")
        try:
            enforce_limit(organization, "integrations")
        except PlanLimitExceeded as exc:
            messages.error(request, str(exc))
            return redirect("integration-list")
        name = request.POST.get("name", "").strip() or "Integration"
        if Integration.objects.filter(organization=organization, name__iexact=name).exists():
            messages.error(request, "An integration with that name already exists.")
            return redirect("integration-list")
        try:
            config = json.loads(request.POST.get("config") or "{}")
        except json.JSONDecodeError:
            config = {}
        try:
            Integration.objects.create(
                organization=organization,
                name=name,
                provider=request.POST.get("provider") or "webhook",
                config=config if isinstance(config, dict) else {},
                credential_ref=request.POST.get("credential_ref", "").strip(),
                created_by=request.user,
            )
        except IntegrityError:
            messages.error(request, "An integration with that name already exists.")
            return redirect("integration-list")
        messages.success(request, "Integration created. Credentials remain server-side.")
        return redirect("integration-list")
    return render(
        request,
        "integrations/list.html",
        {
            "integrations": organization.integrations.all(),
            "providers": Integration.PROVIDER_CHOICES,
            "deliveries": organization.webhook_deliveries.all()[:10],
            "can_manage": role_at_least((get_membership(request).role if get_membership(request) else "viewer"), "admin"),
            "can_test": role_at_least((get_membership(request).role if get_membership(request) else "viewer"), "manager"),
        },
    )


@login_required
@role_required("admin")
def integration_delete(request, integration_id):
    organization = get_organization(request)
    integration = get_object_or_404(Integration, pk=integration_id, organization=organization)
    if request.method == "POST":
        integration.delete()
        messages.success(request, "Integration removed.")
    return redirect("integration-list")


@login_required
@organization_required
def integration_test(request, integration_id):
    organization = get_organization(request)
    integration = get_object_or_404(Integration, pk=integration_id, organization=organization)
    if request.method == "POST":
        membership = get_membership(request)
        if not membership or not role_at_least(membership.role, "manager"):
            messages.error(request, "Managers can test integrations.")
            return redirect("integration-list")
        try:
            result = test_integration(integration, request.user)
            message = result.message if isinstance(result, dict) and "message" in result else "Integration test completed."
            messages.success(request, message)
        except Exception as exc:
            messages.error(request, str(exc))
    return redirect("integration-list")
