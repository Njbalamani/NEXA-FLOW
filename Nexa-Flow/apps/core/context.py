def organization_context(request):
    from apps.core.permissions import get_organization

    context = {"active_organization": None, "organization_memberships": []}
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return context

    try:
        memberships = list(
            user.organization_memberships.select_related("organization")
            .filter(is_active=True)
            .order_by("organization__name")
        )
        context["organization_memberships"] = memberships
        context["active_organization"] = get_organization(request) or (memberships[0].organization if memberships else None)
    except Exception:
        pass
    return context
