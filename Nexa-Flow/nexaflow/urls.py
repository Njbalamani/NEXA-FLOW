from django.conf import settings
from django.urls import include, path
from django.views.generic import TemplateView

from apps.api import urls as api_urls
from apps.health import views as health_views

urlpatterns = [
    path("", include("apps.web.urls")),
    path("accounts/", include("apps.accounts.urls")),
    path("dashboard/", include("apps.analytics.urls")),
    path("workflows/", include("apps.workflows.urls")),
    path("tasks/", include("apps.tasks.urls")),
    path("integrations/", include("apps.integrations.urls")),
    path("notifications/", include("apps.notifications.urls")),
    path("organization/", include("apps.organizations.urls")),
    path("ai/", include("apps.ai.urls")),
    path("billing/", include("apps.billing.urls")),
    path("api/v1/", include(api_urls)),
    path("api/webhooks/<str:token>/", health_views.webhook_dispatch, name="webhook-dispatch"),
    path("health/", health_views.health, name="health"),
    path("robots.txt", TemplateView.as_view(template_name="robots.txt", content_type="text/plain")),
]

if settings.HAS_SPECTACULAR:
    from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView

    urlpatterns += [
        path("api/schema/", SpectacularAPIView.as_view(), name="api-schema"),
        path("api/docs/", SpectacularSwaggerView.as_view(url_name="api-schema"), name="api-docs"),
        path("api/redoc/", SpectacularRedocView.as_view(url_name="api-schema"), name="api-redoc"),
    ]
