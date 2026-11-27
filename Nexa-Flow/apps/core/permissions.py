from rest_framework.permissions import BasePermission

ROLE_RANK = {
    "viewer": 10,
    "member": 20,
    "manager": 30,
    "admin": 40,
    "owner": 50,
}
ALL_ROLES = tuple(ROLE_RANK)


def role_at_least(role: str, minimum: str) -> bool:
    return ROLE_RANK.get(role, 0) >= ROLE_RANK.get(minimum, 99)


def _header(request, name: str):
    headers = getattr(request, "headers", None)
    if headers is not None:
        return headers.get(name)
    return getattr(request, "META", {}).get("HTTP_" + name.upper().replace("-", "_"))


def _query_param(request, name: str):
    params = getattr(request, "query_params", None)
    if params is None:
        params = getattr(request, "GET", {})
    return params.get(name)


def get_membership(request):
    from apps.organizations.models import OrganizationMember

    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return None
    if hasattr(request, "nexaflow_membership"):
        return request.nexaflow_membership

    queryset = OrganizationMember.objects.select_related("organization", "user").filter(user=user, is_active=True)
    organization_id = _header(request, "X-Organization-ID") or _query_param(request, "organization")
    if not organization_id:
        organization_id = getattr(user, "active_organization_id", None)
    if organization_id:
        membership = queryset.filter(organization_id=organization_id).first()
    else:
        membership = queryset.order_by("organization__name").first()

    request.nexaflow_membership = membership
    request.nexaflow_organization = membership.organization if membership else None
    return membership


def get_organization(request):
    membership = get_membership(request)
    return membership.organization if membership else None


class IsOrgMember(BasePermission):
    message = "You must be an active member of an organization."

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and (request.user.is_superuser or get_membership(request))
        )

    def has_object_permission(self, request, view, obj):
        organization = get_organization(request)
        if organization is None:
            return False
        owner = getattr(obj, "organization", None) or getattr(obj, "org", None)
        if owner is not None:
            return owner.pk == organization.pk
        return True


class HasOrgRole(BasePermission):
    """Role gate. Set `minimum_role` or `allowed_roles` on the view."""

    message = "Your organization role does not allow this action."
    minimum_role = "member"
    allowed_roles = None

    def has_permission(self, request, view):
        membership = get_membership(request)
        if getattr(request.user, "is_superuser", False):
            return True
        if not membership:
            return False
        allowed = getattr(view, "allowed_roles", self.allowed_roles)
        if allowed:
            return membership.role in allowed
        minimum = getattr(view, "minimum_role", self.minimum_role)
        return role_at_least(membership.role, minimum)

    def has_object_permission(self, request, view, obj):
        return self.has_permission(request, view) and IsOrgMember().has_object_permission(request, view, obj)


def organization_queryset(queryset, request, field_name: str = "organization"):
    organization = get_organization(request)
    if organization is None:
        return queryset.none()
    return queryset.filter(**{field_name: organization})
