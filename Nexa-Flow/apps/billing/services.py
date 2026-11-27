from __future__ import annotations

import uuid
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.audit.services import log_action
from apps.billing.models import Payment, Subscription
from apps.workflows.models import WorkflowExecution


@transaction.atomic
def create_test_payment(subscription: Subscription, plan: str, user=None, card_number: str = "") -> Payment:
    if plan not in Subscription.PLAN_PRICES:
        raise ValueError("Unsupported plan.")
    digits = "".join(char for char in str(card_number) if char.isdigit())
    last4 = digits[-4:] or "4242"
    failed = bool(card_number) and len(digits) >= 12 and last4 == "0002"

    payment = Payment.objects.create(
        organization=subscription.organization,
        subscription=subscription,
        provider="test",
        provider_reference="test_" + uuid.uuid4().hex[:20],
        amount=Subscription.PLAN_PRICES[plan],
        currency="USD",
        status="failed" if failed else "succeeded",
        description=f"Plan upgrade to {plan.upper()} (test gateway)",
        metadata={"card_last4": last4, "test_mode": True, "plan": plan},
    )
    if not failed:
        now = timezone.now()
        subscription.plan = plan
        subscription.status = Subscription.STATUS_ACTIVE
        subscription.seats = subscription.limits["seats"]
        subscription.cancel_at_period_end = False
        subscription.current_period_start = now
        subscription.current_period_end = now + timedelta(days=30)
        subscription.save()
    log_action(
        user,
        "payment.test_charge",
        payment,
        {"plan": plan, "status": payment.status, "amount": str(payment.amount)},
        subscription.organization,
    )
    return payment


def usage_summary(organization) -> dict:
    now = timezone.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return {
        "workflows": organization.workflows.count(),
        "executions_this_month": WorkflowExecution.objects.filter(
            workflow__organization=organization, created_at__gte=month_start
        ).count(),
        "integrations": organization.integrations.count(),
        "seats": organization.members.filter(is_active=True).count(),
    }
