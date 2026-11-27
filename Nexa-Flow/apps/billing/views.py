from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from apps.core.decorators import organization_required
from apps.core.permissions import get_membership, get_organization, role_at_least

from .models import Subscription
from .services import create_test_payment, usage_summary


@login_required
@organization_required
def billing_view(request):
    organization = get_organization(request)
    subscription, _ = Subscription.objects.get_or_create(
        organization=organization,
        defaults={"plan": Subscription.PLAN_FREE, "status": Subscription.STATUS_ACTIVE},
    )
    if request.method == "POST":
        membership = get_membership(request)
        if not membership or not role_at_least(membership.role, "admin"):
            messages.error(request, "Only organization admins can change the plan.")
            return redirect("billing")
        requested_plan = request.POST.get("plan") or Subscription.PLAN_PRO
        if requested_plan == subscription.plan:
            messages.info(request, f"{subscription.get_plan_display()} is already your current plan.")
            return redirect("billing")
        try:
            payment = create_test_payment(
                subscription,
                requested_plan,
                request.user,
                request.POST.get("card_number", ""),
            )
            if payment.status == "succeeded":
                messages.success(request, "Test payment succeeded. Your plan is active.")
            else:
                messages.error(request, "Test card was declined. No plan changes were made.")
        except ValueError as exc:
            messages.error(request, str(exc))
        return redirect("billing")

    summary = usage_summary(organization)
    limits = subscription.limits
    usage_percent = {}
    for label, key in (
        ("workflows", "workflows"),
        ("executions_per_month", "executions_this_month"),
        ("integrations", "integrations"),
        ("seats", "seats"),
    ):
        usage_percent[label] = min(100, int(summary.get(key, 0) / limits[label] * 100)) if limits.get(label) else 0
    return render(
        request,
        "billing/index.html",
        {
            "subscription": subscription,
            "plans": Subscription.PLAN_CHOICES,
            "prices": Subscription.PLAN_PRICES,
            "payments": organization.payments.all(),
            "usage": summary,
            "usage_percent": usage_percent,
            "limits": limits,
        },
    )
