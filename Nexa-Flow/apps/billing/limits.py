from __future__ import annotations

from django.utils import timezone

from apps.workflows.models import WorkflowExecution

from .models import Subscription


class PlanLimitExceeded(Exception):
    pass


def current_usage(organization) -> dict:
    month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return {
        "workflows": organization.workflows.count(),
        "executions_per_month": WorkflowExecution.objects.filter(
            workflow__organization=organization, created_at__gte=month_start
        ).count(),
        "integrations": organization.integrations.count(),
        "seats": organization.members.filter(is_active=True).count(),
    }


def enforce_limit(organization, resource: str, amount: int = 1) -> None:
    subscription = Subscription.objects.filter(organization=organization).first()
    if not subscription:
        return
    current = current_usage(organization).get(resource, 0)
    if not subscription.allows(resource, current + amount - 1):
        raise PlanLimitExceeded(f"{resource.replace('_', ' ').title()} limit reached for your current plan.")
