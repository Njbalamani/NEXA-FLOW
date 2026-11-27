from __future__ import annotations

from decimal import Decimal

from django.db import models
from django.utils import timezone


class Subscription(models.Model):
    PLAN_FREE = "free"
    PLAN_PRO = "pro"
    PLAN_ENTERPRISE = "enterprise"
    PLAN_CHOICES = (
        (PLAN_FREE, "Free"),
        (PLAN_PRO, "Pro"),
        (PLAN_ENTERPRISE, "Enterprise"),
    )
    STATUS_ACTIVE = "active"
    STATUS_CHOICES = (
        ("trialing", "Trialing"),
        (STATUS_ACTIVE, "Active"),
        ("past_due", "Past due"),
        ("cancelled", "Cancelled"),
    )
    PLAN_LIMITS = {
        PLAN_FREE: {"workflows": 5, "executions_per_month": 100, "integrations": 3, "seats": 5},
        PLAN_PRO: {"workflows": 100, "executions_per_month": 10000, "integrations": 50, "seats": 50},
        PLAN_ENTERPRISE: {"workflows": 100000, "executions_per_month": 1000000, "integrations": 1000, "seats": 1000},
    }
    PLAN_PRICES = {PLAN_FREE: Decimal("0.00"), PLAN_PRO: Decimal("49.00"), PLAN_ENTERPRISE: Decimal("299.00")}

    organization = models.OneToOneField("organizations.Organization", on_delete=models.CASCADE, related_name="subscription")
    plan = models.CharField(max_length=20, choices=PLAN_CHOICES, default=PLAN_FREE)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    seats = models.PositiveIntegerField(default=5)
    current_period_start = models.DateTimeField(default=timezone.now)
    current_period_end = models.DateTimeField(null=True, blank=True)
    cancel_at_period_end = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.organization.name} · {self.plan} ({self.status})"

    @property
    def limits(self):
        return self.PLAN_LIMITS.get(self.plan, self.PLAN_LIMITS[self.PLAN_FREE])

    @property
    def price(self):
        return self.PLAN_PRICES.get(self.plan, Decimal("0.00"))

    def allows(self, resource: str, current_count: int) -> bool:
        if self.status not in {self.STATUS_ACTIVE, "trialing"}:
            return False
        return current_count < int(self.limits.get(resource, 0))


class Payment(models.Model):
    PROVIDER_CHOICES = (("test", "Test gateway"), ("stripe", "Stripe"), ("paypal", "PayPal"))
    STATUS_CHOICES = (
        ("pending", "Pending"),
        ("succeeded", "Succeeded"),
        ("failed", "Failed"),
        ("refunded", "Refunded"),
    )

    organization = models.ForeignKey("organizations.Organization", on_delete=models.CASCADE, related_name="payments")
    subscription = models.ForeignKey(Subscription, on_delete=models.SET_NULL, null=True, blank=True, related_name="payments")
    provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES, default="test")
    provider_reference = models.CharField(max_length=120, unique=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=10, default="USD")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    description = models.CharField(max_length=200)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.provider_reference} · {self.amount} {self.currency} ({self.status})"
