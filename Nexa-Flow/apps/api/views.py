from __future__ import annotations

import json

from django.urls import reverse
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import generics, status, viewsets
from rest_framework.authtoken.models import Token
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import SAFE_METHODS, AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.ai.services import explain_failure, recommend_automations, suggest_improvements
from apps.api.pagination import NexaPagination
from apps.analytics.services import dashboard_metrics
from apps.audit.models import AuditLog
from apps.audit.services import log_action
from apps.billing.limits import PlanLimitExceeded
from apps.billing.models import Payment, Subscription
from apps.billing.services import create_test_payment
from apps.core.permissions import HasOrgRole, IsOrgMember, get_organization, organization_queryset
from apps.integrations.models import Integration, WebhookDelivery
from apps.integrations.services import integration_credentials, retry_webhook_delivery, test_integration
from apps.notifications.models import Notification
from apps.organizations.models import Organization, OrganizationMember, Team, Workspace
from apps.organizations.services import create_personal_organization
from apps.tasks.models import Task, TaskEvent
from apps.tasks.services import change_task_status
from apps.workflows.models import (
    Workflow,
    WorkflowConnection,
    WorkflowExecution,
    WorkflowNode,
    WorkflowTrigger,
    WorkflowVersion,
)
from apps.workflows.services import (
    cancel_execution,
    create_workflow_from_template,
    duplicate_workflow,
    execute_workflow_async,
    restore_workflow_version,
    retry_execution,
    save_workflow_definition,
    workflow_definition,
)
from apps.workflows.validation import WorkflowValidationError, validate_definition, validate_workflow

from apps.accounts.models import User

from .serializers import (
    AIExplainSerializer,
    AIGenerateSerializer,
    BulkTaskStatusSerializer,
    AnalyticsSerializer,
    ApiInfoSerializer,
    AuditLogSerializer,
    ExecutionStepSerializer,
    IntegrationSerializer,
    LoginSerializer,
    NotificationSerializer,
    OrganizationMemberSerializer,
    OrganizationSerializer,
    PaymentSerializer,
    RegisterSerializer,
    SubscriptionSerializer,
    TaskEventSerializer,
    TaskSerializer,
    TaskStatusSerializer,
    TeamSerializer,
    TestPaymentSerializer,
    UserSerializer,
    WebhookDeliverySerializer,
    WorkflowConnectionSerializer,
    WorkflowDefinitionSerializer,
    WorkflowExecutionSerializer,
    WorkflowImportSerializer,
    WorkflowNodeSerializer,
    WorkflowSerializer,
    WorkflowTemplateUseSerializer,
    WorkflowTriggerSerializer,
    WorkflowVersionSerializer,
    WorkspaceSerializer,
)


class OrgScopedMixin:
    organization_field = "organization"
    assign_organization = True
    write_minimum_role = "member"
    custom_action_role = "manager"
    throttle_scope = "api"
    _write_actions = {
        "definition", "execute", "duplicate", "status", "bulk_status", "test",
        "cancel", "retry", "import_workflow", "mark_all_read", "restore", "use_template",
    }
    _admin_actions = {"credentials_status"}

    def get_permissions(self):
        action = getattr(self, "action", "")
        minimum = None
        if action in {"create", "update", "partial_update", "destroy"}:
            minimum = self.write_minimum_role
        elif action in self._write_actions:
            if action == "definition" and getattr(self.request, "method", "GET") in SAFE_METHODS:
                minimum = None
            else:
                minimum = self.custom_action_role
        elif action in self._admin_actions:
            minimum = "admin"
        if minimum:
            self.permission_classes = [IsAuthenticated, HasOrgRole]
            self.minimum_role = minimum
        else:
            self.permission_classes = [IsAuthenticated, IsOrgMember]
        return super().get_permissions()

    def get_queryset(self):
        queryset = super().get_queryset()
        return organization_queryset(queryset, self.request, self.organization_field)

    def save_context(self):
        context = self.get_serializer_context()
        context["request"] = self.request
        return context

    def perform_create(self, serializer):
        kwargs = {}
        if self.assign_organization and "__" not in self.organization_field:
            kwargs[self.organization_field] = get_organization(self.request)
        model = serializer.Meta.model
        if hasattr(model, "created_by"):
            kwargs["created_by"] = self.request.user
        obj = serializer.save(**kwargs)
        log_action(self.request.user, f"{model.__name__.lower()}.created", obj, {}, get_organization(self.request))

    def perform_update(self, serializer):
        obj = serializer.save()
        log_action(
            self.request.user,
            f"{obj.__class__.__name__.lower()}.updated",
            obj,
            {},
            get_organization(self.request),
        )

    def perform_destroy(self, instance):
        log_action(
            self.request.user,
            f"{instance.__class__.__name__.lower()}.deleted",
            instance,
            {},
            get_organization(self.request),
        )
        instance.delete()


class RegisterView(generics.CreateAPIView):
    serializer_class = RegisterSerializer
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "auth"

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        token, _ = Token.objects.get_or_create(user=user)
        return Response(
            {"token": token.key, "user": UserSerializer(user).data},
            status=status.HTTP_201_CREATED,
        )


@extend_schema(request=LoginSerializer, responses=dict)
class LoginView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "auth"

    def post(self, request):
        serializer = LoginSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        token, _ = Token.objects.get_or_create(user=user)
        return Response({"token": token.key, "user": UserSerializer(user).data})


@extend_schema(request=None, responses=dict)
class LogoutView(APIView):
    throttle_scope = "auth"

    def post(self, request):
        from django.contrib.auth import logout as django_logout

        if request.auth and hasattr(request.auth, "delete"):
            request.auth.delete()
        if request.user.is_authenticated:
            django_logout(request)
        return Response({"logged_out": True})


class MeView(generics.RetrieveUpdateAPIView):
    serializer_class = UserSerializer

    def get_object(self):
        return self.request.user


class OrganizationViewSet(OrgScopedMixin, viewsets.ModelViewSet):
    serializer_class = OrganizationSerializer
    assign_organization = False
    write_minimum_role = "admin"
    search_fields = ("name", "description")
    ordering_fields = ("name", "created_at")

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return Organization.objects.none()
        if user.is_superuser:
            return Organization.objects.all()
        return Organization.objects.filter(members__user=user, members__is_active=True).distinct()

    def perform_create(self, serializer):
        organization = create_personal_organization(self.request.user, serializer.validated_data.get("name"))
        serializer.instance = organization
        log_action(self.request.user, "organization.created", organization, {}, organization)


class OrganizationMemberViewSet(OrgScopedMixin, viewsets.ModelViewSet):
    serializer_class = OrganizationMemberSerializer
    write_minimum_role = "admin"

    def get_queryset(self):
        return organization_queryset(
            OrganizationMember.objects.select_related("user", "organization"), self.request, "organization"
        )

    def perform_create(self, serializer):
        serializer.save(organization=get_organization(self.request), invited_by=self.request.user)
        log_action(
            self.request.user,
            "organization_member.created",
            serializer.instance,
            {},
            get_organization(self.request),
        )


class WorkspaceViewSet(OrgScopedMixin, viewsets.ModelViewSet):
    serializer_class = WorkspaceSerializer
    write_minimum_role = "manager"
    queryset = Workspace.objects.all()


class TeamViewSet(OrgScopedMixin, viewsets.ModelViewSet):
    serializer_class = TeamSerializer
    write_minimum_role = "manager"
    queryset = Team.objects.all()

    def get_queryset(self):
        return organization_queryset(
            Team.objects.prefetch_related("members"), self.request, "organization"
        )


class WorkflowViewSet(OrgScopedMixin, viewsets.ModelViewSet):
    serializer_class = WorkflowSerializer
    write_minimum_role = "manager"
    queryset = Workflow.objects.all()
    search_fields = ("name", "description")
    ordering_fields = ("name", "status", "created_at", "updated_at", "last_executed_at")

    def get_queryset(self):
        queryset = organization_queryset(
            Workflow.objects.select_related("workspace", "created_by").prefetch_related("nodes", "triggers"),
            self.request,
            "organization",
        )
        is_template = self.request.query_params.get("is_template")
        if is_template is not None:
            queryset = queryset.filter(is_template=is_template.lower() in {"1", "true", "yes"})
        return queryset

    def perform_create(self, serializer):
        organization = get_organization(self.request)
        subscription = Subscription.objects.filter(organization=organization).first()
        if subscription and not subscription.allows("workflows", organization.workflows.count()):
            raise ValidationError("Workflow limit reached for your current plan.")
        super().perform_create(serializer)

    @action(detail=True, methods=["get"])
    def export(self, request, pk=None):
        workflow = self.get_object()
        data = workflow_definition(workflow)
        return Response({key: value for key, value in data.items() if key not in {"id", "slug", "version"}})

    @action(detail=False, methods=["post"], url_path="import")
    def import_workflow(self, request):
        organization = get_organization(request)
        if not organization:
            raise PermissionDenied("Organization context is required.")
        serializer = WorkflowImportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        workflow = serializer.create_workflow(organization, request.user)
        return Response(WorkflowSerializer(workflow).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["get"])
    def templates(self, request):
        queryset = self.filter_queryset(self.get_queryset().filter(is_template=True))
        page = self.paginate_queryset(queryset)
        serializer = self.get_serializer(page if page is not None else queryset, many=True)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

    @action(detail=True, methods=["post"], url_path="use-template")
    def use_template(self, request, pk=None):
        template = self.get_object()
        if not template.is_template:
            raise ValidationError("Workflow is not a template.")
        organization = get_organization(request) or template.organization
        subscription = Subscription.objects.filter(organization=organization).first()
        if subscription and not subscription.allows("workflows", organization.workflows.count()):
            raise ValidationError("Workflow limit reached for your current plan.")
        serializer = WorkflowTemplateUseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        workflow = create_workflow_from_template(
            template,
            organization,
            request.user,
            serializer.validated_data.get("name") or None,
        )
        return Response(WorkflowSerializer(workflow).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get", "put"], url_path="definition")
    def definition(self, request, pk=None):
        workflow = self.get_object()
        if request.method == "GET":
            data = workflow_definition(workflow)
            data["validation"] = validate_workflow(workflow)
            return Response(data)
        serializer = WorkflowDefinitionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            workflow = save_workflow_definition(workflow, serializer.validated_data["definition"], request.user)
        except WorkflowValidationError as exc:
            raise ValidationError({"errors": exc.errors}) from exc
        data = workflow_definition(workflow)
        data["validation"] = validate_workflow(workflow)
        return Response(data)

    @action(detail=True, methods=["get"])
    def validate(self, request, pk=None):
        return Response(validate_workflow(self.get_object()))

    @action(detail=True, methods=["post"], url_path="validate-draft")
    def validate_draft(self, request, pk=None):
        self.get_object()
        serializer = WorkflowDefinitionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(validate_definition(serializer.validated_data["definition"]))

    @action(detail=True, methods=["post"])
    def execute(self, request, pk=None):
        workflow = self.get_object()
        validation = validate_workflow(workflow)
        if not validation["valid"]:
            raise ValidationError({"errors": validation["errors"]})
        organization = workflow.organization
        subscription = Subscription.objects.filter(organization=organization).first()
        from apps.workflows.models import WorkflowExecution
        from django.utils import timezone

        month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        used = WorkflowExecution.objects.filter(workflow__organization=organization, created_at__gte=month_start).count()
        if subscription and not subscription.allows("executions_per_month", used):
            raise ValidationError("Execution limit reached for your current plan.")
        payload = request.data.get("input") or {}
        if not isinstance(payload, dict):
            raise ValidationError({"input": "Must be an object."})
        execution = execute_workflow_async(workflow, input_data=payload, triggered_by=request.user)
        return Response(
            {
                "execution_id": execution.pk,
                "status": execution.status,
                "execution_url": reverse("execution-detail", args=[execution.pk]),
            },
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=True, methods=["post"])
    def duplicate(self, request, pk=None):
        workflow = self.get_object()
        clone = duplicate_workflow(workflow, request.user, request.data.get("name"))
        return Response(WorkflowSerializer(clone).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"], url_path="executions")
    def executions(self, request, pk=None):
        workflow = self.get_object()
        queryset = workflow.executions.select_related("trigger", "triggered_by").prefetch_related("steps")
        page = self.paginate_queryset(queryset)
        serializer = WorkflowExecutionSerializer(page if page is not None else queryset, many=True)
        return self.get_paginated_response(serializer.data) if page is not None else Response(serializer.data)


class WorkflowNodeViewSet(OrgScopedMixin, viewsets.ModelViewSet):
    serializer_class = WorkflowNodeSerializer
    organization_field = "workflow__organization"
    assign_organization = False
    queryset = WorkflowNode.objects.all()


class WorkflowConnectionViewSet(OrgScopedMixin, viewsets.ModelViewSet):
    serializer_class = WorkflowConnectionSerializer
    organization_field = "workflow__organization"
    assign_organization = False
    queryset = WorkflowConnection.objects.all()


class WorkflowTriggerViewSet(OrgScopedMixin, viewsets.ModelViewSet):
    serializer_class = WorkflowTriggerSerializer
    organization_field = "workflow__organization"
    assign_organization = False
    queryset = WorkflowTrigger.objects.all()


class WorkflowVersionViewSet(OrgScopedMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = WorkflowVersionSerializer
    organization_field = "workflow__organization"
    assign_organization = False
    queryset = WorkflowVersion.objects.all()

    def get_queryset(self):
        queryset = organization_queryset(
            WorkflowVersion.objects.select_related("workflow", "created_by"),
            self.request,
            "workflow__organization",
        )
        workflow_id = self.request.query_params.get("workflow")
        if workflow_id:
            queryset = queryset.filter(workflow_id=workflow_id)
        return queryset

    @action(detail=True, methods=["post"])
    def restore(self, request, pk=None):
        version = self.get_object()
        workflow = restore_workflow_version(version, request.user)
        return Response(
            {
                "workflow_id": workflow.pk,
                "version": workflow.version,
                "restored_from": version.version,
            }
        )


class WorkflowExecutionViewSet(OrgScopedMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = WorkflowExecutionSerializer
    organization_field = "workflow__organization"
    assign_organization = False
    queryset = WorkflowExecution.objects.all()
    search_fields = ("workflow__name", "trigger_type", "status", "error")
    ordering_fields = ("created_at", "started_at", "finished_at", "status")

    def get_queryset(self):
        return organization_queryset(
            WorkflowExecution.objects.select_related("workflow", "trigger", "triggered_by").prefetch_related("steps"),
            self.request,
            "workflow__organization",
        )

    @action(detail=True, methods=["get"])
    def steps(self, request, pk=None):
        execution = self.get_object()
        queryset = execution.steps.all()
        paginator = NexaPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        serializer = ExecutionStepSerializer(page if page is not None else queryset, many=True)
        return paginator.get_paginated_response(serializer.data) if page is not None else Response(serializer.data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        execution = self.get_object()
        try:
            cancel_execution(execution, request.user)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        return Response({"execution_id": execution.pk, "status": execution.status})

    @action(detail=True, methods=["post"])
    def retry(self, request, pk=None):
        execution = self.get_object()
        try:
            new_execution = retry_execution(execution, request.user)
        except (ValueError, PlanLimitExceeded) as exc:
            raise ValidationError(str(exc)) from exc
        return Response(
            {"execution_id": new_execution.pk, "status": new_execution.status, "retried_from": execution.pk},
            status=status.HTTP_202_ACCEPTED,
        )


class TaskViewSet(OrgScopedMixin, viewsets.ModelViewSet):
    serializer_class = TaskSerializer
    write_minimum_role = "member"
    queryset = Task.objects.all()
    search_fields = ("title", "description")
    ordering_fields = ("created_at", "updated_at", "due_at", "status", "priority")

    def get_queryset(self):
        queryset = organization_queryset(
            Task.objects.select_related("assignee", "workflow", "created_by"), self.request, "organization"
        )
        status_filter = self.request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        return queryset

    def perform_create(self, serializer):
        super().perform_create(serializer)
        task = serializer.instance
        TaskEvent.objects.create(task=task, actor=self.request.user, event="created", data={"title": task.title})

    @action(detail=True, methods=["post"])
    def status(self, request, pk=None):
        task = self.get_object()
        serializer = TaskStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        change_task_status(task, serializer.validated_data["status"], request.user)
        return Response(TaskSerializer(task).data)

    @action(detail=False, methods=["post"], url_path="bulk-status")
    def bulk_status(self, request):
        serializer = BulkTaskStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        tasks = self.get_queryset().filter(pk__in=serializer.validated_data["ids"])
        updated = []
        for task in tasks:
            change_task_status(task, serializer.validated_data["status"], request.user)
            updated.append(task)
        return Response({"updated": len(updated), "status": serializer.validated_data["status"]})

    @action(detail=True, methods=["get"], url_path="events")
    def events(self, request, pk=None):
        task = self.get_object()
        queryset = task.events.all()
        paginator = NexaPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        serializer = TaskEventSerializer(page if page is not None else queryset, many=True)
        return paginator.get_paginated_response(serializer.data) if page is not None else Response(serializer.data)


class IntegrationViewSet(OrgScopedMixin, viewsets.ModelViewSet):
    serializer_class = IntegrationSerializer
    write_minimum_role = "admin"
    queryset = Integration.objects.all()
    search_fields = ("name", "provider", "credential_ref")
    ordering_fields = ("name", "created_at", "updated_at")

    @action(detail=True, methods=["post"])
    def test(self, request, pk=None):
        integration = self.get_object()
        try:
            result = test_integration(integration, request.user)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        if isinstance(result, dict):
            return Response(result)
        return Response(
            {
                "success": result.success,
                "status_code": result.status_code,
                "error": result.error,
                "delivery_id": result.pk,
            }
        )

    @action(detail=True, methods=["get"], url_path="credentials-status")
    def credentials_status(self, request, pk=None):
        integration = self.get_object()
        credentials = integration_credentials(integration)
        return Response({"configured": bool(credentials), "keys": sorted(credentials.keys())})


class WebhookDeliveryViewSet(OrgScopedMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = WebhookDeliverySerializer
    queryset = WebhookDelivery.objects.all()
    search_fields = ("event", "url", "error")
    ordering_fields = ("created_at", "status_code", "success")

    def get_queryset(self):
        queryset = organization_queryset(
            WebhookDelivery.objects.select_related("integration"), self.request, "organization"
        )
        integration_id = self.request.query_params.get("integration")
        if integration_id:
            queryset = queryset.filter(integration_id=integration_id)
        return queryset

    @action(detail=True, methods=["post"])
    def retry(self, request, pk=None):
        delivery = self.get_object()
        try:
            retry = retry_webhook_delivery(delivery, request.user)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        return Response(WebhookDeliverySerializer(retry).data, status=status.HTTP_201_CREATED)


class NotificationViewSet(OrgScopedMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = NotificationSerializer
    queryset = Notification.objects.all()
    search_fields = ("title", "body", "category")
    ordering_fields = ("created_at", "read_at", "category")

    def get_queryset(self):
        queryset = organization_queryset(Notification.objects.all(), self.request, "organization")
        return queryset.filter(user=self.request.user) | queryset.filter(user__isnull=True)

    @action(detail=True, methods=["post"], url_path="mark-read")
    def mark_read(self, request, pk=None):
        notification = self.get_object()
        notification.read_at = timezone.now()
        notification.save(update_fields=["read_at"])
        return Response(NotificationSerializer(notification).data)

    @action(detail=False, methods=["post"], url_path="mark-all-read")
    def mark_all_read(self, request):
        updated = self.get_queryset().filter(read_at__isnull=True).update(read_at=timezone.now())
        return Response({"updated": updated})


class SubscriptionViewSet(OrgScopedMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = SubscriptionSerializer
    queryset = Subscription.objects.all()


class PaymentViewSet(OrgScopedMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = PaymentSerializer
    queryset = Payment.objects.all()


@extend_schema(request=TestPaymentSerializer, responses=PaymentSerializer)
class TestPaymentView(APIView):
    throttle_scope = "billing"
    permission_classes = [IsAuthenticated, HasOrgRole]
    minimum_role = "admin"

    def post(self, request):
        serializer = TestPaymentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        organization = get_organization(request)
        if not organization:
            raise PermissionDenied("Organization context is required.")
        subscription, _ = Subscription.objects.get_or_create(
            organization=organization,
            defaults={"plan": Subscription.PLAN_FREE, "status": Subscription.STATUS_ACTIVE},
        )
        requested_plan = serializer.validated_data["plan"]
        if requested_plan == subscription.plan:
            return Response(
                {"detail": f"{subscription.get_plan_display()} is already your current plan."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        payment = create_test_payment(
            subscription,
            requested_plan,
            request.user,
            serializer.validated_data.get("card_number", ""),
        )
        return Response(PaymentSerializer(payment).data, status=status.HTTP_201_CREATED)


class AuditLogViewSet(OrgScopedMixin, viewsets.ReadOnlyModelViewSet):
    serializer_class = AuditLogSerializer
    queryset = AuditLog.objects.all()


@extend_schema(responses=dict)
class AnalyticsView(APIView):
    throttle_scope = "api"

    def get(self, request):
        organization = get_organization(request)
        if not organization:
            raise PermissionDenied("Organization context is required.")
        metrics = dashboard_metrics(organization)
        metrics["recent_activity"] = [
            {
                "id": item.pk,
                "workflow": item.workflow.name,
                "status": item.status,
                "error": item.error,
                "created_at": item.created_at,
            }
            for item in metrics["recent_activity"]
        ]
        metrics["top_workflows"] = [
            {"id": item.pk, "name": item.name, "status": item.status, "runs": item.runs}
            for item in metrics["top_workflows"]
        ]
        serializer = AnalyticsSerializer(metrics)
        return Response(
            {
                **serializer.data,
                "charts": json.loads(metrics["chart_data"]),
                "workflow_status": json.loads(metrics["workflow_status_data"]),
                "task_status": json.loads(metrics["task_status_data"]),
                "recent_activity": metrics["recent_activity"],
                "top_workflows": metrics["top_workflows"],
            }
        )


@extend_schema(request=AIGenerateSerializer, responses=dict)
class AIAssistantView(APIView):
    throttle_scope = "ai"

    def get_permissions(self):
        if getattr(self, "request", None) and self.request.data.get("save"):
            self.permission_classes = [IsAuthenticated, HasOrgRole]
            self.minimum_role = "manager"
        else:
            self.permission_classes = [IsAuthenticated, IsOrgMember]
        return super().get_permissions()

    def post(self, request):
        organization = get_organization(request)
        if not organization:
            raise PermissionDenied("Organization context is required.")
        serializer = AIGenerateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = serializer.create_workflow(organization, request.user)
        return Response(result, status=status.HTTP_201_CREATED if result.get("workflow_id") else status.HTTP_200_OK)


@extend_schema(request=AIExplainSerializer, responses=dict)
class AIExplainView(APIView):
    throttle_scope = "ai"

    def post(self, request):
        organization = get_organization(request)
        if not organization:
            raise PermissionDenied("Organization context is required.")
        serializer = AIExplainSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.explain(organization, request.user))


@extend_schema(responses=dict)
class AIRecommendationsView(APIView):
    throttle_scope = "ai"

    def get(self, request):
        organization = get_organization(request)
        if not organization:
            raise PermissionDenied("Organization context is required.")
        return Response({"recommendations": recommend_automations(organization)})


@extend_schema(responses=dict)
class AIImprovementsView(APIView):
    throttle_scope = "ai"

    def get(self, request, workflow_id):
        organization = get_organization(request)
        workflow = Workflow.objects.filter(pk=workflow_id, organization=organization).first()
        if not workflow:
            raise PermissionDenied("Workflow not found in this organization.")
        return Response({"suggestions": suggest_improvements(workflow)})


@extend_schema(responses=ApiInfoSerializer)
class ApiInfoView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "api"

    def get(self, request):
        return Response(
            ApiInfoSerializer(
                {
                    "service": "NexaFlow API",
                    "version": "v1",
                    "authentication": "Token or session authentication. Send X-Organization-ID for multi-org context.",
                }
            ).data
        )
