from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError
from django.shortcuts import get_object_or_404, redirect, render

from apps.accounts.models import User
from apps.core.permissions import get_membership, get_organization
from apps.core.utils import safe_redirect

from .models import OrganizationMember, Team, Workspace


@login_required
def organization_settings(request):
    organization = get_organization(request)
    if not organization:
        return redirect("dashboard")
    membership = get_membership(request)

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "workspace" and membership.role in {"owner", "admin", "manager"}:
            name = request.POST.get("name", "").strip()
            if name:
                Workspace.objects.create(
                    organization=organization, name=name, description=request.POST.get("description", ""), created_by=request.user
                )
                messages.success(request, "Workspace created.")
        elif action == "member" and membership.role in {"owner", "admin"}:
            email = request.POST.get("email", "").strip().lower()
            role = request.POST.get("role", "member")
            assignable_roles = {"viewer", "member", "manager", "admin"}
            if role not in assignable_roles:
                messages.error(request, "Choose a valid assignable role.")
                return redirect("organization-settings")
            user = User.objects.filter(email__iexact=email).first()
            if user:
                try:
                    OrganizationMember.objects.create(
                        organization=organization, user=user, role=role, invited_by=request.user, is_active=True
                    )
                    messages.success(request, f"{user.email} added to the organization.")
                except IntegrityError:
                    OrganizationMember.objects.filter(organization=organization, user=user).update(is_active=True, role=role)
                    messages.info(request, "Existing membership updated.")
            else:
                messages.error(request, "No user exists with that email address.")
        return redirect("organization-settings")

    return render(
        request,
        "organizations/settings.html",
        {
            "organization": organization,
            "members": organization.members.select_related("user"),
            "workspaces": organization.workspaces.all(),
            "teams": Team.objects.filter(organization=organization),
            "membership": membership,
            "roles": [
                choice
                for choice in OrganizationMember._meta.get_field("role").choices
                if choice[0] in {"viewer", "member", "manager", "admin"}
            ],
            "memberships": organization.members.all(),
            "can_manage_members": membership.role in {"owner", "admin"},
            "can_manage_workspaces": membership.role in {"owner", "admin", "manager"},
        },
    )


@login_required
def switch_organization(request, organization_id):
    membership = OrganizationMember.objects.filter(
        organization_id=organization_id, user=request.user, is_active=True
    ).first()
    if membership:
        request.user.active_organization = membership.organization
        request.user.save(update_fields=["active_organization"])
        messages.success(request, f"Switched to {membership.organization.name}.")
    return redirect(safe_redirect(request.GET.get("next")))
