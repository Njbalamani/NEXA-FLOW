from __future__ import annotations

from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from apps.ai.services import explain_failure, generate_workflow as generate_workflow_service
from apps.analytics.services import dashboard_metrics
from apps.audit.models import AuditLog
from apps.audit.services import log_action
from apps.billing.models import Payment, Subscription
from apps.billing.services import create_test_payment
from apps.integrations.models import Integration, WebhookDelivery
from apps.integrations.services import public_integration, set_integration_credentials, test_integration
from apps.notifications.models import Notification
from apps.organizations.models import Organization, OrganizationMember, Team, Workspace
from apps.tasks.models import Task, TaskEvent
from apps.workflows.models import (
    ExecutionStep,
    Workflow,
    WorkflowConnection,
    WorkflowExecution,
    WorkflowNode,
    WorkflowTrigger,
    WorkflowVersion,
)
from apps.workflows.services import save_workflow_definition, workflow_definition
from apps.workflows.validation import validate_definition, validate_workflow

from apps.accounts.models import User


def _request_organization(context: dict):
    request = context.get("request")
    if not request:
        return None
    organization = getattr(request, "nexaflow_organization", None)
    if organization is None:
        from apps.core.permissions import get_organization

        organization = get_organization(request)
    return organization


def _validate_same_organization(value, organization, label: str):
    if value is None or organization is None:
        return
    if getattr(value, "organization_id", None) != organization.pk:
        raise serializers.ValidationError({label: "Object does not belong to the active organization."})


def _validate_org_member(value, organization, label: str):
    if value is None or organization is None:
        return
    if not OrganizationMember.objects.filter(organization=organization, user=value, is_active=True).exists():
        raise serializers.ValidationError({label: "User is not an active member of the active organization."})


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "email", "full_name", "phone", "timezone", "is_staff", "created_at")
        read_only_fields = ("id", "is_staff", "created_at")


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, validators=[validate_password])
    organization_name = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = User
        fields = ("id", "email", "full_name", "password", "organization_name")
        read_only_fields = ("id",)

    def create(self, validated_data):
        from apps.organizations.services import create_personal_organization

        organization_name = validated_data.pop("organization_name", "")
        user = User.objects.create_user(**validated_data)
        organization = create_personal_organization(user, organization_name or None)
        user.active_organization = organization
        user.save(update_fields=["active_organization"])
        return user


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, style={"input_type": "password"})

    def validate(self, attrs):
        user = authenticate(username=attrs["email"].lower(), password=attrs["password"])
        if not user or not user.is_active:
            raise serializers.ValidationError("Invalid email or password.")
        attrs["user"] = user
        return attrs


class OrganizationSerializer(serializers.ModelSerializer):
    member_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Organization
        fields = ("id", "name", "slug", "description", "website", "owner", "member_count", "created_at", "updated_at")
        read_only_fields = ("id", "slug", "owner", "created_at", "updated_at")


class OrganizationMemberSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source="user.email", read_only=True)
    user_name = serializers.CharField(source="user.get_full_name", read_only=True)

    class Meta:
        model = OrganizationMember
        fields = ("id", "organization", "user", "user_email", "user_name", "role", "title", "is_active", "joined_at")
        read_only_fields = ("id", "organization", "joined_at")

    def validate(self, attrs):
        request = self.context.get("request")
        organization = attrs.get("organization") or (getattr(request, "nexaflow_organization", None) if request else None)
        role = attrs.get("role", "member")
        valid_roles = set(dict(OrganizationMember._meta.get_field("role").choices))
        if role not in valid_roles or role == "owner":
            raise serializers.ValidationError({"role": "Choose an assignable organization role."})
        user = attrs.get("user")
        if organization and user and not OrganizationMember.objects.filter(organization=organization, user=user, is_active=True).exists():
            request = self.context.get("request")
            if request and not (getattr(request.user, "is_superuser", False) or self._is_admin(request)):
                raise serializers.ValidationError("Only organization admins can add members.")
        return attrs

    def _is_admin(self, request):
        from apps.core.permissions import get_membership, role_at_least

        membership = get_membership(request)
        return bool(membership and role_at_least(membership.role, "admin"))


class WorkspaceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Workspace
        fields = ("id", "organization", "name", "slug", "description", "created_by", "created_at")
        read_only_fields = ("id", "organization", "slug", "created_at")


class TeamSerializer(serializers.ModelSerializer):
    class Meta:
        model = Team
        fields = ("id", "organization", "workspace", "name", "description", "members", "created_at")
        read_only_fields = ("id", "organization", "created_at")

    def validate(self, attrs):
        organization = _request_organization(self.context)
        _validate_same_organization(attrs.get("workspace"), organization, "workspace")
        for member in attrs.get("members", []):
            _validate_org_member(member, organization, "members")
        return attrs


class WorkflowSerializer(serializers.ModelSerializer):
    node_count = serializers.IntegerField(read_only=True)
    execution_count = serializers.IntegerField(read_only=True)
    webhook_url = serializers.SerializerMethodField()

    class Meta:
        model = Workflow
        fields = (
            "id", "organization", "workspace", "name", "slug", "description", "status", "definition", "version",
            "webhook_token", "webhook_url", "is_template", "node_count", "execution_count", "created_by",
            "created_at", "updated_at", "last_executed_at",
        )
        read_only_fields = ("id", "organization", "slug", "version", "webhook_token", "created_by", "created_at", "updated_at", "last_executed_at")

    def validate(self, attrs):
        _validate_same_organization(attrs.get("workspace"), _request_organization(self.context), "workspace")
        return attrs

    def get_webhook_url(self, obj) -> str:
        from django.conf import settings

        return f"{settings.NEXAFLOW['FRONTEND_URL'].rstrip('/')}/api/webhooks/{obj.webhook_token}/"


class WorkflowVersionSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkflowVersion
        fields = ("id", "workflow", "version", "definition", "created_by", "created_at")
        read_only_fields = fields


class WorkflowDefinitionSerializer(serializers.Serializer):
    definition = serializers.JSONField()

    def validate_definition(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("Definition must be an object.")
        return value


class WorkflowImportSerializer(serializers.Serializer):
    definition = serializers.JSONField()
    name = serializers.CharField(max_length=180, required=False, allow_blank=True)

    def validate_definition(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("Definition must be an object.")
        result = validate_definition(value)
        if not result["valid"]:
            raise serializers.ValidationError(result["errors"])
        return value

    def create_workflow(self, organization, user):
        from apps.billing.models import Subscription

        from apps.workflows.services import create_workflow_from_definition

        subscription = Subscription.objects.filter(organization=organization).first()
        if subscription and not subscription.allows("workflows", organization.workflows.count()):
            raise serializers.ValidationError("Workflow limit reached for your current plan.")
        return create_workflow_from_definition(
            organization,
            self.validated_data["definition"],
            user=user,
            name=self.validated_data.get("name"),
        )


class WorkflowTemplateUseSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=180, required=False, allow_blank=True)


class WorkflowNodeSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkflowNode
        fields = ("id", "workflow", "key", "type", "name", "config", "position_x", "position_y", "created_at", "updated_at")
        read_only_fields = ("id", "created_at", "updated_at")

    def validate(self, attrs):
        _validate_same_organization(attrs.get("workflow"), _request_organization(self.context), "workflow")
        return attrs


class WorkflowConnectionSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkflowConnection
        fields = ("id", "workflow", "source", "target", "source_handle", "target_handle", "label", "created_at")
        read_only_fields = ("id", "created_at")

    def validate(self, attrs):
        organization = _request_organization(self.context)
        workflow = attrs.get("workflow")
        _validate_same_organization(workflow, organization, "workflow")
        for field in ("source", "target"):
            related = attrs.get(field)
            if not related:
                continue
            if workflow and related.workflow_id != workflow.pk:
                raise serializers.ValidationError({field: "Node belongs to a different workflow."})
            if related.workflow.organization_id != organization.pk:
                raise serializers.ValidationError({field: "Object does not belong to the active organization."})
        return attrs


class WorkflowTriggerSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkflowTrigger
        fields = ("id", "workflow", "name", "type", "config", "webhook_secret", "is_active", "last_run_at", "created_at")
        read_only_fields = ("id", "webhook_secret", "last_run_at", "created_at")

    def validate(self, attrs):
        _validate_same_organization(attrs.get("workflow"), _request_organization(self.context), "workflow")
        return attrs


class ExecutionStepSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExecutionStep
        fields = (
            "id", "execution", "node", "node_key", "node_name", "node_type", "status", "input", "output", "error",
            "attempt", "started_at", "finished_at",
        )


class WorkflowExecutionSerializer(serializers.ModelSerializer):
    steps = ExecutionStepSerializer(many=True, read_only=True)
    workflow_name = serializers.CharField(source="workflow.name", read_only=True)

    class Meta:
        model = WorkflowExecution
        fields = (
            "id", "workflow", "workflow_name", "trigger", "trigger_type", "status", "input", "context", "result", "error",
            "attempts", "duration_ms", "triggered_by", "started_at", "finished_at", "created_at", "steps",
        )
        read_only_fields = fields


class ExecuteWorkflowSerializer(serializers.Serializer):
    input = serializers.JSONField(default=dict, required=False)


class TaskSerializer(serializers.ModelSerializer):
    assignee_email = serializers.EmailField(source="assignee.email", read_only=True)

    class Meta:
        model = Task
        fields = (
            "id", "organization", "workspace", "workflow", "execution", "title", "description", "assignee",
            "assignee_email", "created_by", "status", "priority", "due_at", "completed_at", "metadata",
            "created_at", "updated_at",
        )
        read_only_fields = ("id", "organization", "completed_at", "created_at", "updated_at")

    def validate(self, attrs):
        organization = _request_organization(self.context)
        for field in ("workspace", "workflow"):
            _validate_same_organization(attrs.get(field), organization, field)
        workflow = attrs.get("workflow")
        execution = attrs.get("execution")
        if execution and organization and execution.workflow.organization_id != organization.pk:
            raise serializers.ValidationError({"execution": "Object does not belong to the active organization."})
        if workflow and execution and execution.workflow_id != workflow.pk:
            raise serializers.ValidationError({"execution": "Execution belongs to a different workflow."})
        _validate_org_member(attrs.get("assignee"), organization, "assignee")
        return attrs


class TaskStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=Task.STATUS_CHOICES)


class BulkTaskStatusSerializer(serializers.Serializer):
    ids = serializers.ListField(child=serializers.IntegerField(), allow_empty=False, max_length=100)
    status = serializers.ChoiceField(choices=Task.STATUS_CHOICES)


class TaskEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaskEvent
        fields = ("id", "task", "actor", "event", "data", "created_at")


class IntegrationSerializer(serializers.ModelSerializer):
    credentials = serializers.JSONField(write_only=True, required=False)

    class Meta:
        model = Integration
        fields = (
            "id", "organization", "name", "provider", "config", "credentials", "credential_ref", "is_active",
            "created_by", "created_at", "updated_at",
        )
        read_only_fields = ("id", "organization", "created_by", "created_at", "updated_at")

    def validate_name(self, value):
        organization = _request_organization(self.context)
        if organization is None:
            return value
        queryset = Integration.objects.filter(organization=organization, name__iexact=value)
        if self.instance:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise serializers.ValidationError("An integration with this name already exists.")
        return value

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data.update(public_integration(instance))
        return data

    def create(self, validated_data):
        from apps.billing.models import Subscription

        organization = validated_data.get("organization") or _request_organization(self.context)
        subscription = Subscription.objects.filter(organization=organization).first() if organization else None
        if subscription and not subscription.allows("integrations", organization.integrations.count()):
            raise serializers.ValidationError("Integration limit reached for your current plan.")
        credentials = validated_data.pop("credentials", None)
        integration = Integration.objects.create(**validated_data)
        if credentials is not None:
            set_integration_credentials(integration, credentials, validated_data.get("created_by"))
        return integration

    def update(self, instance, validated_data):
        credentials = validated_data.pop("credentials", None)
        for key, value in validated_data.items():
            setattr(instance, key, value)
        instance.save()
        if credentials is not None:
            set_integration_credentials(instance, credentials, self.context["request"].user)
        return instance


class WebhookDeliverySerializer(serializers.ModelSerializer):
    class Meta:
        model = WebhookDelivery
        fields = ("id", "organization", "integration", "url", "event", "payload", "status_code", "success", "error", "created_at")
        read_only_fields = fields


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ("id", "organization", "user", "title", "body", "category", "channel", "data", "read_at", "created_at")
        read_only_fields = fields


class SubscriptionSerializer(serializers.ModelSerializer):
    limits = serializers.JSONField(read_only=True)
    usage = serializers.SerializerMethodField()

    class Meta:
        model = Subscription
        fields = (
            "id", "organization", "plan", "status", "seats", "limits", "usage", "current_period_start",
            "current_period_end", "cancel_at_period_end", "created_at", "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at", "limits", "usage")

    def get_usage(self, obj) -> dict:
        from apps.billing.services import usage_summary

        return usage_summary(obj.organization)


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = (
            "id", "organization", "subscription", "provider", "provider_reference", "amount", "currency", "status",
            "description", "metadata", "created_at",
        )
        read_only_fields = fields


class TestPaymentSerializer(serializers.Serializer):
    plan = serializers.ChoiceField(choices=Subscription.PLAN_CHOICES, default=Subscription.PLAN_PRO)
    card_number = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_card_number(self, value):
        if value and not value.replace(" ", "").isdigit():
            raise serializers.ValidationError("Test card numbers contain digits only.")
        return value


class AuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditLog
        fields = ("id", "organization", "actor", "action", "target_type", "target_id", "ip_address", "user_agent", "metadata", "created_at")
        read_only_fields = fields


class AnalyticsSerializer(serializers.Serializer):
    total_workflows = serializers.IntegerField()
    active_workflows = serializers.IntegerField()
    completed_executions = serializers.IntegerField()
    failed_executions = serializers.IntegerField()
    pending_tasks = serializers.IntegerField()
    success_rate = serializers.FloatField()


class AIGenerateSerializer(serializers.Serializer):
    description = serializers.CharField(min_length=10, max_length=4000)
    save = serializers.BooleanField(default=False)
    name = serializers.CharField(max_length=180, required=False, allow_blank=True)

    def create_workflow(self, organization, user):
        result = generate_workflow_service(self.validated_data["description"])
        if not result["validation"]["valid"]:
            raise serializers.ValidationError({"workflow": result["validation"]["errors"]})
        if self.validated_data["save"]:
            from apps.billing.models import Subscription

            subscription = Subscription.objects.filter(organization=organization).first()
            if subscription and not subscription.allows("workflows", organization.workflows.count()):
                raise serializers.ValidationError("Workflow limit reached for your current plan.")
            definition = dict(result["definition"])
            definition["name"] = self.validated_data.get("name") or definition.get("name") or "AI Workflow"
            workflow = Workflow.objects.create(
                organization=organization,
                workspace=organization.workspaces.first(),
                name=definition["name"],
                description=definition.get("description", ""),
                status="draft",
                created_by=user,
            )
            save_workflow_definition(workflow, definition, user)
            result["workflow_id"] = workflow.pk
        return result


class AIExplainSerializer(serializers.Serializer):
    execution_id = serializers.IntegerField()

    def explain(self, organization, user):
        execution = WorkflowExecution.objects.filter(pk=self.validated_data["execution_id"], workflow__organization=organization).first()
        if not execution:
            raise serializers.ValidationError("Execution not found in this organization.")
        return {"explanation": explain_failure(execution)}


class ApiInfoSerializer(serializers.Serializer):
    service = serializers.CharField()
    version = serializers.CharField()
    authentication = serializers.CharField()
