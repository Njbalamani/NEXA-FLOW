from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils import timezone

from apps.core.decorators import organization_required
from apps.core.permissions import get_organization

from .models import Notification


@login_required
@organization_required
def notification_list(request):
    organization = get_organization(request)
    notifications = Notification.objects.filter(organization=organization).filter(user=request.user) | Notification.objects.filter(
        organization=organization, user__isnull=True
    )
    notifications = notifications.distinct().select_related("user")
    if request.method == "POST":
        ids = request.POST.getlist("ids")
        if "read_all" in request.POST:
            notifications.update(read_at=timezone.now())
        elif ids:
            notifications.filter(pk__in=ids).update(read_at=timezone.now())
        return redirect("notification-list")
    return render(request, "notifications/list.html", {"notifications": notifications})
