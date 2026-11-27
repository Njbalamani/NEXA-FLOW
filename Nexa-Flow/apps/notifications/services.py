from __future__ import annotations

from django.conf import settings
from django.core.mail import send_mail

from .models import Notification


def notify(
    organization,
    user=None,
    title: str = "Notification",
    body: str = "",
    category: str = "general",
    data: dict | None = None,
    channel: str = "in_app",
    send_email: bool = False,
) -> Notification | None:
    notification = Notification.objects.create(
        organization=organization,
        user=user,
        title=title[:200],
        body=body,
        category=category,
        channel=channel,
        data=data or {},
    )
    if send_email and user and user.email:
        send_mail(
            title,
            body,
            settings.NEXAFLOW["EMAIL_FROM"],
            [user.email],
            fail_silently=True,
        )
    return notification
