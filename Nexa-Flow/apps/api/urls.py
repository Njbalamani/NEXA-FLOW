from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

app_name = "api"

router = DefaultRouter()
router.register("organizations", views.OrganizationViewSet, basename="organization")
router.register("members", views.OrganizationMemberViewSet, basename="member")
router.register("workspaces", views.WorkspaceViewSet, basename="workspace")
router.register("teams", views.TeamViewSet, basename="team")
router.register("workflows", views.WorkflowViewSet, basename="workflow")
router.register("nodes", views.WorkflowNodeViewSet, basename="node")
router.register("connections", views.WorkflowConnectionViewSet, basename="connection")
router.register("triggers", views.WorkflowTriggerViewSet, basename="trigger")
router.register("workflow-versions", views.WorkflowVersionViewSet, basename="workflowversion")
router.register("executions", views.WorkflowExecutionViewSet, basename="execution")
router.register("tasks", views.TaskViewSet, basename="task")
router.register("integrations", views.IntegrationViewSet, basename="integration")
router.register("webhook-deliveries", views.WebhookDeliveryViewSet, basename="webhookdelivery")
router.register("notifications", views.NotificationViewSet, basename="notification")
router.register("subscriptions", views.SubscriptionViewSet, basename="subscription")
router.register("payments", views.PaymentViewSet, basename="payment")
router.register("audit-logs", views.AuditLogViewSet, basename="auditlog")

urlpatterns = [
    path("", views.ApiInfoView.as_view(), name="api-info"),
    path("auth/register/", views.RegisterView.as_view(), name="auth-register"),
    path("auth/login/", views.LoginView.as_view(), name="auth-login"),
    path("auth/logout/", views.LogoutView.as_view(), name="auth-logout"),
    path("auth/me/", views.MeView.as_view(), name="auth-me"),
    path("billing/test-payment/", views.TestPaymentView.as_view(), name="test-payment"),
    path("analytics/", views.AnalyticsView.as_view(), name="analytics"),
    path("ai/generate/", views.AIAssistantView.as_view(), name="ai-generate"),
    path("ai/explain-failure/", views.AIExplainView.as_view(), name="ai-explain"),
    path("ai/recommendations/", views.AIRecommendationsView.as_view(), name="ai-recommendations"),
    path("ai/improvements/<int:workflow_id>/", views.AIImprovementsView.as_view(), name="ai-improvements"),
    path("", include(router.urls)),
]
