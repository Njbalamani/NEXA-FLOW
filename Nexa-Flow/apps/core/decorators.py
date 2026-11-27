from functools import wraps

from django.contrib import messages
from django.shortcuts import redirect

from .permissions import get_membership, role_at_least


def organization_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        membership = get_membership(request)
        if not membership:
            messages.error(request, "Join or create an organization to continue.")
            return redirect("dashboard")
        request.nexaflow_membership = membership
        request.nexaflow_organization = membership.organization
        return view_func(request, *args, **kwargs)

    return wrapper


def role_required(minimum: str):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            membership = get_membership(request)
            if not membership or not role_at_least(membership.role, minimum):
                messages.error(request, "You do not have permission to do that.")
                return redirect("dashboard")
            request.nexaflow_membership = membership
            request.nexaflow_organization = membership.organization
            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator
