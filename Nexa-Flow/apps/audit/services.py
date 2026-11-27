from __future__ import annotations

from apps.core.middleware import get_current_request

from .models import AuditLog


def log_action(actor=None, action: str = "unknown", target=None, metadata: dict | None = None, organization=None) -> AuditLog | None:
    request = get_current_request()
    target_type = target.__class__.__name__ if target is not None else ""
    target_id = str(getattr(target, "pk", "") or "")

    if organization is None and target is not None:
        organization = getattr(target, "organization", None) or getattr(target, "org", None)
    if organization is None and request is not None:
        from apps.core.permissions import get_organization

        organization = get_organization(request)
    if organization is None and actor is not None and getattr(actor, "active_organization_id", None):
        organization = actor.active_organization

    if organization is None:
        from apps.organizations.models import OrganizationMember

        membership = OrganizationMember.objects.filter(user=actor, is_active=True).select_related("organization").first()
        organization = membership.organization if membership else None
    if organization is None:
        return None

    ip_address = None
    user_agent = ""
    if request is not None:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        ip_address = (forwarded.split(",")[0].strip() if forwarded else request.META.get("REMOTE_ADDR")) or None
        user_agent = request.META.get("HTTP_USER_AGENT", "")[:1000]

    return AuditLog.objects.create(
        organization=organization,
        actor=actor if actor is not None and getattr(actor, "is_authenticated", True) else None,
        action=action,
        target_type=target_type,
        target_id=target_id,
        ip_address=ip_address,
        user_agent=user_agent,
        metadata=metadata or {},
    )
