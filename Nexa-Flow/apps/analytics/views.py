from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from apps.core.permissions import get_organization
from apps.organizations.services import create_personal_organization

from .services import dashboard_metrics


@login_required
def dashboard(request):
    organization = get_organization(request)
    if not organization:
        if request.method == "POST":
            name = request.POST.get("name", "").strip()
            if not name:
                messages.error(request, "Organization name is required.")
            else:
                organization = create_personal_organization(request.user, name)
                request.user.active_organization = organization
                request.user.save(update_fields=["active_organization"])
                messages.success(request, "Organization created.")
                return redirect("dashboard")
        return render(request, "onboarding.html")

    context = dashboard_metrics(organization)
    context["organization"] = organization
    return render(request, "dashboard.html", context)
