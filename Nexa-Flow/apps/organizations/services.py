from __future__ import annotations

from django.db import transaction

from .models import Organization, OrganizationMember, Workspace


@transaction.atomic
def create_personal_organization(user, name: str | None = None):
    existing = OrganizationMember.objects.filter(user=user, is_active=True).select_related("organization").first()
    if existing:
        return existing.organization

    display_name = name or f"{user.get_full_name()}'s Workspace"
    organization = Organization.objects.create(name=display_name, owner=user, description="Default NexaFlow workspace")
    OrganizationMember.objects.create(organization=organization, user=user, role="owner", title="Owner")
    Workspace.objects.create(
        organization=organization, name="Main Workspace", description="Default workspace for automation workflows", created_by=user
    )

    from apps.billing.models import Subscription

    Subscription.objects.get_or_create(
        organization=organization,
        defaults={"plan": Subscription.PLAN_FREE, "status": Subscription.STATUS_ACTIVE},
    )
    return organization
