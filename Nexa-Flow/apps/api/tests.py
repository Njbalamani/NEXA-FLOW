import hashlib
import hmac
import json
import time
from datetime import timedelta
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from apps.ai.services import generate_workflow
from apps.audit.models import AuditLog
from apps.core.crypto import _fernet
from apps.billing.models import Payment, Subscription
from apps.integrations.models import Integration, WebhookDelivery
from apps.organizations.models import OrganizationMember
from apps.notifications.models import Notification
from apps.organizations.services import create_personal_organization
from apps.tasks.models import Task
from apps.core.utils import safe_redirect
from apps.workflows.engine import execute_workflow, run_execution
from apps.workflows.expressions import UnsafeExpression, safe_eval
from apps.workflows.models import Workflow, WorkflowExecution, WorkflowNode, WorkflowTrigger, WorkflowVersion
from apps.workflows.services import save_workflow_definition
from apps.workflows.tasks import is_schedule_due, run_due_workflows

User = get_user_model()
TEST_NEXAFLOW = dict(settings.NEXAFLOW)
TEST_NEXAFLOW["ENCRYPTION_KEY"] = "test-encryption-key"


def make_user(email: str, password: str = "StrongPass123!"):
    user = User.objects.create_user(email=email, password=password, full_name=email.split("@")[0].title())
    organization = create_personal_organization(user)
    user.active_organization = organization
    user.save(update_fields=["active_organization"])
    return user, organization


def auth(client, user):
    token, _ = Token.objects.get_or_create(user=user)
    client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")


def simple_definition(status_value="active", action="log"):
    return {
        "name": "Test workflow",
        "description": "Automated test workflow",
        "status": status_value,
        "nodes": [
            {"key": "trigger_1", "type": "trigger", "name": "Start", "config": {}, "position": {"x": 0, "y": 0}},
            {
                "key": "action_1",
                "type": "action",
                "name": "Record result",
                "config": {"action": action, "message": "step completed"} if action == "log" else {"action": action},
                "position": {"x": 240, "y": 0},
            },
        ],
        "connections": [{"source": "trigger_1", "target": "action_1", "source_handle": "out"}],
        "triggers": [{"name": "Manual", "type": "manual", "config": {}, "is_active": True}],
    }


@override_settings(CELERY_TASK_ALWAYS_EAGER=True, CELERY_TASK_EAGER_PROPAGATES=True)
class AuthenticationTests(APITestCase):
    def test_register_creates_user_organization_and_token(self):
        response = self.client.post(
            "/api/v1/auth/register/",
            {
                "email": "owner@example.com",
                "full_name": "Owner User",
                "password": "StrongPass123!",
                "organization_name": "Acme Ops",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn("token", response.data)
        user = User.objects.get(email="owner@example.com")
        self.assertTrue(user.check_password("StrongPass123!"))
        self.assertEqual(user.organization_memberships.first().role, "owner")
        self.assertEqual(user.active_organization.name, "Acme Ops")
        self.assertEqual(Subscription.objects.get(organization=user.active_organization).plan, "free")

    def test_login_returns_token_and_me_works(self):
        user, _ = make_user("login@example.com")
        response = self.client.post(
            "/api/v1/auth/login/", {"email": "login@example.com", "password": "StrongPass123!"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {response.data['token']}")
        me = self.client.get("/api/v1/auth/me/")
        self.assertEqual(me.status_code, status.HTTP_200_OK)
        self.assertEqual(me.data["email"], user.email)

    def test_invalid_login_is_rejected(self):
        make_user("badlogin@example.com")
        response = self.client.post(
            "/api/v1/auth/login/", {"email": "badlogin@example.com", "password": "WrongPassword123!"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


@override_settings(CELERY_TASK_ALWAYS_EAGER=True, CELERY_TASK_EAGER_PROPAGATES=True)
class WorkflowApiTests(APITestCase):
    def setUp(self):
        self.user, self.organization = make_user("workflows@example.com")
        auth(self.client, self.user)

    def test_create_workflow_via_api(self):
        response = self.client.post(
            "/api/v1/workflows/",
            {"name": "Invoice approval", "description": "Approve invoices", "status": "draft"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        workflow = Workflow.objects.get(pk=response.data["id"])
        self.assertEqual(workflow.organization, self.organization)
        self.assertEqual(workflow.created_by, self.user)

    def test_definition_validation_rejects_invalid_graph(self):
        workflow = Workflow.objects.create(
            organization=self.organization, name="Invalid", created_by=self.user
        )
        invalid = {
            "nodes": [
                {"key": "action_1", "type": "action", "name": "Action", "config": {"action": "log"}}
            ],
            "connections": [],
            "triggers": [],
        }
        response = self.client.put(
            f"/api/v1/workflows/{workflow.pk}/definition/", {"definition": invalid}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(any("trigger" in item.lower() for item in response.data["errors"]))

    def test_definition_save_and_validate(self):
        workflow = Workflow.objects.create(
            organization=self.organization, name="Valid", created_by=self.user
        )
        response = self.client.put(
            f"/api/v1/workflows/{workflow.pk}/definition/",
            {"definition": simple_definition()},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["validation"]["valid"])
        workflow.refresh_from_db()
        self.assertEqual(workflow.status, "active")
        self.assertEqual(workflow.nodes.count(), 2)
        self.assertEqual(workflow.connections.count(), 1)

    def test_builder_flow_links_run_now_to_execution_detail(self):
        workflow = Workflow.objects.create(
            organization=self.organization, name="Builder flow", created_by=self.user
        )
        saved = self.client.put(
            f"/api/v1/workflows/{workflow.pk}/definition/",
            {"definition": simple_definition()},
            format="json",
        )
        self.assertEqual(saved.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(saved.data["version"], 2)

        validated = self.client.post(
            f"/api/v1/workflows/{workflow.pk}/validate-draft/",
            {"definition": simple_definition()},
            format="json",
        )
        self.assertEqual(validated.status_code, status.HTTP_200_OK)
        self.assertTrue(validated.data["valid"])

        executed = self.client.post(f"/api/v1/workflows/{workflow.pk}/execute/", {}, format="json")
        self.assertEqual(executed.status_code, status.HTTP_202_ACCEPTED)
        execution = WorkflowExecution.objects.get(pk=executed.data["execution_id"])
        self.assertEqual(
            executed.data["execution_url"], reverse("execution-detail", args=[execution.pk])
        )
        self.assertEqual(execution.status, "success")
        self.assertEqual(execution.steps.count(), 2)

    def test_execution_persists_steps_and_result(self):
        workflow = Workflow.objects.create(
            organization=self.organization, name="Executable", created_by=self.user
        )
        save_workflow_definition(workflow, simple_definition(), self.user)
        response = self.client.post(
            f"/api/v1/workflows/{workflow.pk}/execute/", {"input": {"order": 10}}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        execution = WorkflowExecution.objects.get(pk=response.data["execution_id"])
        self.assertEqual(execution.status, "success")
        self.assertEqual(execution.input, {"order": 10})
        self.assertEqual(execution.steps.count(), 2)
        self.assertEqual(execution.result["steps"]["action_1"]["message"], "step completed")

    def test_conditional_logic_routes_true_and_false(self):
        definition = {
            "name": "Conditional",
            "status": "active",
            "nodes": [
                {"key": "trigger_1", "type": "trigger", "name": "Start", "config": {}},
                {
                    "key": "condition_1",
                    "type": "condition",
                    "name": "Amount check",
                    "config": {"field": "trigger.payload.amount", "operator": "gte", "value": 100},
                },
                {"key": "high", "type": "action", "name": "High", "config": {"action": "log", "message": "high"}},
                {"key": "low", "type": "action", "name": "Low", "config": {"action": "log", "message": "low"}},
            ],
            "connections": [
                {"source": "trigger_1", "target": "condition_1", "source_handle": "out"},
                {"source": "condition_1", "target": "high", "source_handle": "true"},
                {"source": "condition_1", "target": "low", "source_handle": "false"},
            ],
            "triggers": [{"name": "Manual", "type": "manual", "config": {}}],
        }
        workflow = Workflow.objects.create(organization=self.organization, name="Conditional", created_by=self.user)
        save_workflow_definition(workflow, definition, self.user)

        true_run = execute_workflow(workflow, input_data={"amount": 150})
        self.assertEqual(true_run.status, "success")
        self.assertEqual(true_run.result["steps"]["condition_1"]["branch"], "true")
        self.assertIn("high", true_run.result["steps"])
        self.assertNotIn("low", true_run.result["steps"])

        false_run = execute_workflow(workflow, input_data={"amount": 50})
        self.assertEqual(false_run.status, "success")
        self.assertEqual(false_run.result["steps"]["condition_1"]["branch"], "false")
        self.assertIn("low", false_run.result["steps"])
        self.assertNotIn("high", false_run.result["steps"])

    def test_scheduled_execution(self):
        definition = simple_definition()
        definition["triggers"] = [
            {"name": "Hourly", "type": "schedule", "config": {"mode": "interval", "minutes": 60}, "is_active": True}
        ]
        workflow = Workflow.objects.create(organization=self.organization, name="Scheduled", created_by=self.user)
        save_workflow_definition(workflow, definition, self.user)
        trigger = workflow.triggers.get()
        self.assertTrue(is_schedule_due(trigger))
        result = run_due_workflows()
        self.assertEqual(result["scheduled"], 1)
        trigger.refresh_from_db()
        self.assertIsNotNone(trigger.last_run_at)
        self.assertEqual(workflow.executions.count(), 1)

    def test_webhook_signature_validation(self):
        cache.clear()
        definition = simple_definition()
        definition["triggers"] = [{"name": "Webhook", "type": "webhook", "config": {}, "is_active": True}]
        workflow = Workflow.objects.create(organization=self.organization, name="Webhook", created_by=self.user)
        save_workflow_definition(workflow, definition, self.user)
        trigger = workflow.triggers.get()
        payload = json.dumps({"amount": 250})

        bad = self.client.post(
            f"/api/webhooks/{workflow.webhook_token}/",
            payload,
            content_type="application/json",
            HTTP_X_NEXAFLOW_SIGNATURE="sha256=bad",
        )
        self.assertEqual(bad.status_code, status.HTTP_401_UNAUTHORIZED)

        signature = "sha256=" + hmac.new(trigger.webhook_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        good = self.client.post(
            f"/api/webhooks/{workflow.webhook_token}/",
            payload,
            content_type="application/json",
            HTTP_X_NEXAFLOW_SIGNATURE=signature,
        )
        self.assertEqual(good.status_code, status.HTTP_202_ACCEPTED)
        execution_id = json.loads(good.content)["execution_id"]
        execution = WorkflowExecution.objects.get(pk=execution_id)
        self.assertEqual(execution.status, "success")
        self.assertEqual(execution.input["amount"], 250)

        replay = self.client.post(
            f"/api/webhooks/{workflow.webhook_token}/",
            payload,
            content_type="application/json",
            HTTP_X_NEXAFLOW_SIGNATURE=signature,
        )
        self.assertEqual(replay.status_code, status.HTTP_409_CONFLICT)

    def test_webhook_timestamp_signature_and_cron_step_bounds(self):
        cache.clear()
        definition = simple_definition()
        definition["triggers"] = [{"name": "Webhook", "type": "webhook", "config": {}, "is_active": True}]
        workflow = Workflow.objects.create(organization=self.organization, name="Timestamp", created_by=self.user)
        save_workflow_definition(workflow, definition, self.user)
        trigger = workflow.triggers.get()
        payload = json.dumps({"amount": 10})
        timestamp = str(int(time.time()))
        signed = f"{timestamp}.".encode() + payload.encode()
        signature = "sha256=" + hmac.new(trigger.webhook_secret.encode(), signed, hashlib.sha256).hexdigest()
        response = self.client.post(
            f"/api/webhooks/{workflow.webhook_token}/",
            payload,
            content_type="application/json",
            HTTP_X_NEXAFLOW_SIGNATURE=signature,
            HTTP_X_NEXAFLOW_TIMESTAMP=timestamp,
            HTTP_X_NEXAFLOW_NONCE="nonce-123",
        )
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)

        scheduled = WorkflowTrigger(config={"mode": "cron", "cron": "10/5 * * * *"})
        self.assertFalse(is_schedule_due(scheduled, timezone.now().replace(minute=0, second=0, microsecond=0)))
        self.assertTrue(is_schedule_due(scheduled, timezone.now().replace(minute=10, second=0, microsecond=0)))

    @override_settings(
        NEXAFLOW={**TEST_NEXAFLOW, "WEBHOOK_RATE_LIMIT_PER_MINUTE": 1, "TRUST_X_FORWARDED_FOR": False}
    )
    def test_webhook_rate_limit_ignores_spoofed_forwarded_for(self):
        cache.clear()
        definition = simple_definition()
        definition["triggers"] = [{"name": "Webhook", "type": "webhook", "config": {}, "is_active": True}]
        workflow = Workflow.objects.create(organization=self.organization, name="Rate limit", created_by=self.user)
        save_workflow_definition(workflow, definition, self.user)
        trigger = workflow.triggers.get()
        payload = json.dumps({"amount": 1})
        signature = "sha256=" + hmac.new(trigger.webhook_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()

        first = self.client.post(
            f"/api/webhooks/{workflow.webhook_token}/",
            payload,
            content_type="application/json",
            HTTP_X_NEXAFLOW_SIGNATURE=signature,
            HTTP_X_FORWARDED_FOR="203.0.113.10",
        )
        self.assertEqual(first.status_code, status.HTTP_202_ACCEPTED)

        second = self.client.post(
            f"/api/webhooks/{workflow.webhook_token}/",
            payload,
            content_type="application/json",
            HTTP_X_NEXAFLOW_SIGNATURE=signature,
            HTTP_X_FORWARDED_FOR="203.0.113.11",
        )
        self.assertEqual(second.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    @override_settings(
        NEXAFLOW={**TEST_NEXAFLOW, "WEBHOOK_RATE_LIMIT_PER_MINUTE": 1, "TRUST_X_FORWARDED_FOR": True}
    )
    def test_webhook_rate_limit_can_trust_forwarded_for(self):
        cache.clear()
        definition = simple_definition()
        definition["triggers"] = [{"name": "Webhook", "type": "webhook", "config": {}, "is_active": True}]
        workflow = Workflow.objects.create(organization=self.organization, name="Trusted proxy", created_by=self.user)
        save_workflow_definition(workflow, definition, self.user)
        trigger = workflow.triggers.get()

        for ip, amount in (("198.51.100.10", 1), ("198.51.100.11", 2)):
            payload = json.dumps({"amount": amount})
            signature = "sha256=" + hmac.new(
                trigger.webhook_secret.encode(), payload.encode(), hashlib.sha256
            ).hexdigest()
            response = self.client.post(
                f"/api/webhooks/{workflow.webhook_token}/",
                payload,
                content_type="application/json",
                HTTP_X_NEXAFLOW_SIGNATURE=signature,
                HTTP_X_FORWARDED_FOR=ip,
            )
            self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)

    def test_oversized_expression_is_rejected(self):
        with self.assertRaises(UnsafeExpression):
            safe_eval("[1] * 1000000000", {})


@override_settings(CELERY_TASK_ALWAYS_EAGER=True, NEXAFLOW=TEST_NEXAFLOW)
class TaskAndPermissionTests(APITestCase):
    def setUp(self):
        _fernet.cache_clear()
        self.user, self.organization = make_user("tasks@example.com")
        auth(self.client, self.user)

    def test_task_creation_assignment_and_status_history(self):
        response = self.client.post(
            "/api/v1/tasks/",
            {"title": "Review contract", "description": "Legal check", "priority": "high"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        task = Task.objects.get(pk=response.data["id"])
        self.assertEqual(task.organization, self.organization)
        update = self.client.post(f"/api/v1/tasks/{task.pk}/status/", {"status": "completed"}, format="json")
        self.assertEqual(update.status_code, status.HTTP_200_OK)
        task.refresh_from_db()
        self.assertEqual(task.status, "completed")
        self.assertIsNotNone(task.completed_at)
        self.assertEqual(task.events.count(), 2)

    def test_viewer_cannot_create_workflow(self):
        viewer = User.objects.create_user(email="viewer@example.com", password="StrongPass123!")
        OrganizationMember.objects.create(organization=self.organization, user=viewer, role="viewer")
        viewer.active_organization = self.organization
        viewer.save(update_fields=["active_organization"])
        auth(self.client, viewer)
        response = self.client.post("/api/v1/workflows/", {"name": "Blocked"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(Workflow.objects.filter(organization=self.organization, name="Blocked").count(), 0)

    def test_member_creation_assigns_active_organization(self):
        invitee = User.objects.create_user(email="invitee@example.com", password="StrongPass123!")
        response = self.client.post("/api/v1/members/", {"user": invitee.pk, "role": "member"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(OrganizationMember.objects.get(pk=response.data["id"]).organization, self.organization)

    def test_cannot_attach_node_to_foreign_workflow(self):
        _, other_organization = make_user("other-org@example.com")
        foreign = Workflow.objects.create(
            organization=other_organization, name="Foreign", created_by=self.user
        )
        response = self.client.post(
            "/api/v1/nodes/",
            {"workflow": foreign.pk, "key": "foreign", "type": "trigger", "name": "Foreign", "config": {}},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_viewer_cannot_execute_or_edit_definition(self):
        viewer = User.objects.create_user(email="viewer-actions@example.com", password="StrongPass123!")
        OrganizationMember.objects.create(organization=self.organization, user=viewer, role="viewer")
        viewer.active_organization = self.organization
        viewer.save(update_fields=["active_organization"])
        workflow = Workflow.objects.create(organization=self.organization, name="Gated", created_by=self.user)
        save_workflow_definition(workflow, simple_definition(), self.user)
        auth(self.client, viewer)
        execute = self.client.post(f"/api/v1/workflows/{workflow.pk}/execute/", {}, format="json")
        self.assertEqual(execute.status_code, status.HTTP_403_FORBIDDEN)
        definition = self.client.put(
            f"/api/v1/workflows/{workflow.pk}/definition/", {"definition": simple_definition()}, format="json"
        )
        self.assertEqual(definition.status_code, status.HTTP_403_FORBIDDEN)

    def test_integration_credentials_are_never_returned(self):
        response = self.client.post(
            "/api/v1/integrations/",
            {
                "name": "Slack",
                "provider": "slack",
                "config": {"channel": "ops"},
                "credentials": {"webhook_url": "https://hooks.example.com/secret-token"},
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        content = json.dumps(response.data)
        self.assertNotIn("secret-token", content)
        self.assertNotIn("credentials_encrypted", content)
        integration = Integration.objects.get(pk=response.data["id"])
        self.assertNotEqual(integration.credentials_encrypted, "")

    def test_analytics_uses_real_database_values(self):
        workflow = Workflow.objects.create(
            organization=self.organization, name="Analytics", status="active", created_by=self.user
        )
        save_workflow_definition(workflow, simple_definition(), self.user)
        execute_workflow(workflow, input_data={})
        response = self.client.get("/api/v1/analytics/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["total_workflows"], 1)
        self.assertEqual(response.data["completed_executions"], 1)
        self.assertEqual(response.data["success_rate"], 100.0)
        self.assertEqual(len(response.data["recent_activity"]), 1)
        self.assertEqual(response.data["top_workflows"][0]["runs"], 1)


@override_settings(CELERY_TASK_ALWAYS_EAGER=True)
class BillingAndAITests(APITestCase):
    def setUp(self):
        self.user, self.organization = make_user("billing@example.com")
        auth(self.client, self.user)

    def test_test_gateway_upgrade_and_decline(self):
        response = self.client.post(
            "/api/v1/billing/test-payment/", {"plan": "pro", "card_number": "4242424242424242"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], "succeeded")
        subscription = Subscription.objects.get(organization=self.organization)
        self.assertEqual(subscription.plan, "pro")
        self.assertNotIn("4242424242424242", json.dumps(response.data))

        decline = self.client.post(
            "/api/v1/billing/test-payment/", {"plan": "enterprise", "card_number": "4000000000000002"}, format="json"
        )
        self.assertEqual(decline.status_code, status.HTTP_201_CREATED)
        self.assertEqual(decline.data["status"], "failed")
        subscription.refresh_from_db()
        self.assertEqual(subscription.plan, "pro")

    def test_renewing_current_plan_creates_no_payment(self):
        start = Payment.objects.count()
        response = self.client.post(
            "/api/v1/billing/test-payment/", {"plan": "free", "card_number": "4242424242424242"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Payment.objects.count(), start)

    def test_viewer_cannot_upgrade_or_save_ai_workflow(self):
        viewer = User.objects.create_user(email="viewer-billing@example.com", password="StrongPass123!")
        OrganizationMember.objects.create(organization=self.organization, user=viewer, role="viewer")
        viewer.active_organization = self.organization
        viewer.save(update_fields=["active_organization"])
        auth(self.client, viewer)
        payment = self.client.post(
            "/api/v1/billing/test-payment/",
            {"plan": "pro", "card_number": "4242424242424242"},
            format="json",
        )
        self.assertEqual(payment.status_code, status.HTTP_403_FORBIDDEN)
        save_ai = self.client.post(
            "/api/v1/ai/generate/",
            {"description": "When a request arrives, create a review task for the operations team", "save": True},
            format="json",
        )
        self.assertEqual(save_ai.status_code, status.HTTP_403_FORBIDDEN)
        preview_ai = self.client.post(
            "/api/v1/ai/generate/",
            {"description": "When a request arrives, create a review task for the operations team", "save": False},
            format="json",
        )
        self.assertEqual(preview_ai.status_code, status.HTTP_200_OK)

    def test_ai_generation_is_validated_before_save(self):
        response = self.client.post(
            "/api/v1/ai/generate/",
            {"description": "Every day at 9:00, if amount >= 100, send an email and create a task", "save": True},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data["validation"]["valid"])
        workflow = Workflow.objects.get(pk=response.data["workflow_id"])
        self.assertEqual(workflow.status, "draft")
        self.assertTrue(workflow.nodes.exists())

    def test_local_assistant_rejects_short_description(self):
        with self.assertRaises(ValueError):
            generate_workflow("short")


@override_settings(CELERY_TASK_ALWAYS_EAGER=True, CELERY_TASK_EAGER_PROPAGATES=True)
class AdvancedApiTests(APITestCase):
    def setUp(self):
        self.user, self.organization = make_user("advanced@example.com")
        auth(self.client, self.user)

    def test_openapi_schema_is_served(self):
        response = self.client.get("/api/schema/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn(b"/api/v1/workflows/", response.content)

    def test_workflow_export_and_import(self):
        workflow = Workflow.objects.create(organization=self.organization, name="Source", created_by=self.user)
        save_workflow_definition(workflow, simple_definition(), self.user)

        exported = self.client.get(f"/api/v1/workflows/{workflow.pk}/export/")
        self.assertEqual(exported.status_code, status.HTTP_200_OK)
        self.assertNotIn("id", exported.data)
        self.assertEqual(len(exported.data["nodes"]), 2)

        imported = self.client.post(
            "/api/v1/workflows/import/",
            {"name": "Imported", "definition": exported.data},
            format="json",
        )
        self.assertEqual(imported.status_code, status.HTTP_201_CREATED)
        created = Workflow.objects.get(pk=imported.data["id"])
        self.assertEqual(created.name, "Imported")
        self.assertEqual(created.nodes.count(), 2)
        self.assertEqual(created.connections.count(), 1)

    def test_workflow_versions_are_snapshotted_and_restorable(self):
        workflow = Workflow.objects.create(organization=self.organization, name="Versioned", created_by=self.user)
        first = simple_definition()
        first["name"] = "First version"
        first["nodes"][1]["config"]["message"] = "one"
        save_workflow_definition(workflow, first, self.user)
        second = simple_definition()
        second["name"] = "Second version"
        second["nodes"][1]["config"]["message"] = "two"
        save_workflow_definition(workflow, second, self.user)

        listed = self.client.get(f"/api/v1/workflow-versions/?workflow={workflow.pk}")
        self.assertEqual(listed.status_code, status.HTTP_200_OK)
        self.assertEqual(listed.data["count"], 2)
        self.assertEqual([item["version"] for item in listed.data["results"]], [3, 2])

        old_version = WorkflowVersion.objects.get(workflow=workflow, version=2)
        restored = self.client.post(f"/api/v1/workflow-versions/{old_version.pk}/restore/", {}, format="json")
        self.assertEqual(restored.status_code, status.HTTP_200_OK)
        workflow.refresh_from_db()
        self.assertEqual(workflow.name, "First version")
        self.assertEqual(workflow.version, 4)
        self.assertEqual(workflow.nodes.get(key="action_1").config["message"], "one")
        self.assertEqual(workflow.versions.count(), 3)

    def test_workflow_api_search_and_ordering(self):
        for name in ("Zeta reporting", "Alpha intake"):
            Workflow.objects.create(organization=self.organization, name=name, created_by=self.user)

        searched = self.client.get("/api/v1/workflows/?search=alpha")
        self.assertEqual(searched.status_code, status.HTTP_200_OK)
        self.assertEqual(searched.data["count"], 1)
        self.assertEqual(searched.data["results"][0]["name"], "Alpha intake")

        ordered = self.client.get("/api/v1/workflows/?ordering=name")
        self.assertEqual([item["name"] for item in ordered.data["results"]], ["Alpha intake", "Zeta reporting"])

    @patch("apps.integrations.services.requests.post")
    def test_webhook_delivery_api_logs_and_retries(self, post_mock):
        post_mock.return_value = SimpleNamespace(status_code=200, text="ok")
        integration = Integration.objects.create(
            organization=self.organization,
            name="Delivery hook",
            provider="webhook",
            config={"test_url": "https://example.com/hook"},
            created_by=self.user,
        )
        tested = self.client.post(f"/api/v1/integrations/{integration.pk}/test/", {}, format="json")
        self.assertEqual(tested.status_code, status.HTTP_200_OK)
        delivery_id = tested.data["delivery_id"]

        listed = self.client.get(f"/api/v1/webhook-deliveries/?integration={integration.pk}")
        self.assertEqual(listed.status_code, status.HTTP_200_OK)
        self.assertEqual(listed.data["count"], 1)

        retried = self.client.post(f"/api/v1/webhook-deliveries/{delivery_id}/retry/", {}, format="json")
        self.assertEqual(retried.status_code, status.HTTP_201_CREATED)
        self.assertTrue(retried.data["success"])
        self.assertEqual(WebhookDelivery.objects.count(), 2)

    def test_template_seeding_creates_reusable_workflows(self):
        call_command("seed_templates", organization=self.organization.pk, stdout=StringIO())
        templates = Workflow.objects.filter(organization=self.organization, is_template=True)
        self.assertEqual(templates.count(), 3)
        self.assertTrue(all(template.nodes.exists() for template in templates))

    def test_template_api_lists_and_instantiates_templates(self):
        call_command("seed_templates", organization=self.organization.pk, stdout=StringIO())
        templates = self.client.get("/api/v1/workflows/templates/")
        self.assertEqual(templates.status_code, status.HTTP_200_OK)
        self.assertEqual(templates.data["count"], 3)

        template = Workflow.objects.get(organization=self.organization, name="Approval task router", is_template=True)
        created = self.client.post(
            f"/api/v1/workflows/{template.pk}/use-template/",
            {"name": "My approval flow"},
            format="json",
        )
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)
        self.assertEqual(created.data["name"], "My approval flow")
        self.assertFalse(created.data["is_template"])
        workflow = Workflow.objects.get(pk=created.data["id"])
        self.assertEqual(workflow.nodes.count(), template.nodes.count())
        self.assertEqual(workflow.connections.count(), template.connections.count())
        self.assertEqual(workflow.triggers.count(), template.triggers.count())

        regular = Workflow.objects.create(organization=self.organization, name="Regular", created_by=self.user)
        rejected = self.client.post(
            f"/api/v1/workflows/{regular.pk}/use-template/", {}, format="json"
        )
        self.assertEqual(rejected.status_code, status.HTTP_400_BAD_REQUEST)

    def test_execution_cancel_and_retry(self):
        workflow = Workflow.objects.create(organization=self.organization, name="Executable", created_by=self.user)
        save_workflow_definition(workflow, simple_definition(), self.user)

        queued = WorkflowExecution.objects.create(workflow=workflow, status="queued", triggered_by=self.user)
        cancelled = self.client.post(f"/api/v1/executions/{queued.pk}/cancel/", {}, format="json")
        self.assertEqual(cancelled.status_code, status.HTTP_200_OK)
        queued.refresh_from_db()
        self.assertEqual(queued.status, "cancelled")

        failed = WorkflowExecution.objects.create(
            workflow=workflow, status="failed", input={"amount": 25}, triggered_by=self.user
        )
        retried = self.client.post(f"/api/v1/executions/{failed.pk}/retry/", {}, format="json")
        self.assertEqual(retried.status_code, status.HTTP_202_ACCEPTED)
        new_execution = WorkflowExecution.objects.get(pk=retried.data["execution_id"])
        self.assertEqual(new_execution.input, {"amount": 25})
        self.assertEqual(new_execution.status, "success")

    def test_execution_steps_endpoint_is_paginated(self):
        workflow = Workflow.objects.create(organization=self.organization, name="Paged", created_by=self.user)
        save_workflow_definition(workflow, simple_definition(), self.user)
        executed = self.client.post(f"/api/v1/workflows/{workflow.pk}/execute/", {}, format="json")
        self.assertEqual(executed.status_code, status.HTTP_202_ACCEPTED)

        steps = self.client.get(
            f"/api/v1/executions/{executed.data['execution_id']}/steps/?page_size=1"
        )
        self.assertEqual(steps.status_code, status.HTTP_200_OK)
        self.assertEqual(steps.data["count"], 2)
        self.assertEqual(len(steps.data["results"]), 1)

    def test_cancelled_execution_is_not_overwritten_by_runner(self):
        workflow = Workflow.objects.create(organization=self.organization, name="Cancelled", created_by=self.user)
        save_workflow_definition(workflow, simple_definition(), self.user)
        execution = WorkflowExecution.objects.create(workflow=workflow, status="cancelled", triggered_by=self.user)

        run_execution(execution)
        execution.refresh_from_db()
        self.assertEqual(execution.status, "cancelled")
        self.assertEqual(execution.steps.count(), 0)

    def test_bulk_task_status_and_mark_all_notifications_read(self):
        tasks = [
            Task.objects.create(organization=self.organization, title=f"Task {index}", created_by=self.user)
            for index in range(2)
        ]
        bulk = self.client.post(
            "/api/v1/tasks/bulk-status/",
            {"ids": [task.pk for task in tasks], "status": "completed"},
            format="json",
        )
        self.assertEqual(bulk.status_code, status.HTTP_200_OK)
        self.assertEqual(bulk.data["updated"], 2)
        self.assertTrue(all(Task.objects.get(pk=task.pk).status == "completed" for task in tasks))

        Notification.objects.create(organization=self.organization, title="Unread one")
        Notification.objects.create(organization=self.organization, title="Unread two")
        marked = self.client.post("/api/v1/notifications/mark-all-read/", {}, format="json")
        self.assertEqual(marked.status_code, status.HTTP_200_OK)
        self.assertEqual(marked.data["updated"], 2)
        self.assertEqual(Notification.objects.filter(read_at__isnull=True).count(), 0)


@override_settings(CELERY_TASK_ALWAYS_EAGER=True, CELERY_TASK_EAGER_PROPAGATES=True)
class CoreApiCoverageTests(APITestCase):
    def setUp(self):
        self.user, self.organization = make_user("coverage@example.com")
        auth(self.client, self.user)

    def test_audit_cleanup_command_respects_retention_and_dry_run(self):
        old = AuditLog.objects.create(
            organization=self.organization,
            action="old.action",
            created_at=timezone.now() - timedelta(days=91),
        )
        recent = AuditLog.objects.create(organization=self.organization, action="recent.action")
        output = StringIO()

        call_command("cleanup_audit_logs", "--days=90", "--dry-run", stdout=output)
        self.assertIn("1 audit log entries would be deleted", output.getvalue())
        self.assertTrue(AuditLog.objects.filter(pk=old.pk).exists())

        call_command("cleanup_audit_logs", "--days=90", stdout=StringIO())
        self.assertFalse(AuditLog.objects.filter(pk=old.pk).exists())
        self.assertTrue(AuditLog.objects.filter(pk=recent.pk).exists())

    def test_workspace_and_team_crud(self):
        workspace = self.client.post("/api/v1/workspaces/", {"name": "Operations"}, format="json")
        self.assertEqual(workspace.status_code, status.HTTP_201_CREATED)
        updated = self.client.patch(
            f"/api/v1/workspaces/{workspace.data['id']}/", {"name": "Ops"}, format="json"
        )
        self.assertEqual(updated.status_code, status.HTTP_200_OK)
        self.assertEqual(updated.data["name"], "Ops")

        team = self.client.post(
            "/api/v1/teams/",
            {"name": "Automation", "workspace": workspace.data["id"]},
            format="json",
        )
        self.assertEqual(team.status_code, status.HTTP_201_CREATED)
        listed = self.client.get("/api/v1/teams/")
        self.assertEqual(listed.data["count"], 1)
        deleted = self.client.delete(f"/api/v1/teams/{team.data['id']}/")
        self.assertEqual(deleted.status_code, status.HTTP_204_NO_CONTENT)

    def test_graph_record_crud(self):
        workflow = Workflow.objects.create(organization=self.organization, name="Graph", created_by=self.user)
        start = self.client.post(
            "/api/v1/nodes/",
            {"workflow": workflow.pk, "key": "trigger_1", "type": "trigger", "name": "Start", "config": {}},
            format="json",
        )
        self.assertEqual(start.status_code, status.HTTP_201_CREATED)
        action = self.client.post(
            "/api/v1/nodes/",
            {
                "workflow": workflow.pk,
                "key": "action_1",
                "type": "action",
                "name": "Log",
                "config": {"action": "log", "message": "done"},
            },
            format="json",
        )
        self.assertEqual(action.status_code, status.HTTP_201_CREATED)

        connection = self.client.post(
            "/api/v1/connections/",
            {"workflow": workflow.pk, "source": start.data["id"], "target": action.data["id"]},
            format="json",
        )
        self.assertEqual(connection.status_code, status.HTTP_201_CREATED, connection.data)
        trigger = self.client.post(
            "/api/v1/triggers/",
            {"workflow": workflow.pk, "name": "Manual", "type": "manual", "config": {}},
            format="json",
        )
        self.assertEqual(trigger.status_code, status.HTTP_201_CREATED)

        patched = self.client.patch(
            f"/api/v1/nodes/{action.data['id']}/", {"name": "Record"}, format="json"
        )
        self.assertEqual(patched.status_code, status.HTTP_200_OK)
        deleted = self.client.delete(f"/api/v1/connections/{connection.data['id']}/")
        self.assertEqual(deleted.status_code, status.HTTP_204_NO_CONTENT)

    def test_connection_rejects_node_from_another_organization(self):
        other_user, other_organization = make_user("graph-other@example.com")
        workflow = Workflow.objects.create(organization=self.organization, name="Local", created_by=self.user)
        local = WorkflowNode.objects.create(workflow=workflow, key="start", type="trigger", name="Start")
        foreign_workflow = Workflow.objects.create(
            organization=other_organization, name="Foreign", created_by=other_user
        )
        foreign = WorkflowNode.objects.create(
            workflow=foreign_workflow, key="foreign", type="action", name="Foreign"
        )

        response = self.client.post(
            "/api/v1/connections/",
            {"workflow": workflow.pk, "source": local.pk, "target": foreign.pk},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_ai_support_credentials_and_logout_endpoints(self):
        workflow = Workflow.objects.create(organization=self.organization, name="Support", created_by=self.user)
        failed = WorkflowExecution.objects.create(workflow=workflow, status="failed", error="boom")
        integration = Integration.objects.create(
            organization=self.organization, name="Hook", provider="webhook", config={}
        )

        recommendations = self.client.get("/api/v1/ai/recommendations/")
        self.assertEqual(recommendations.status_code, status.HTTP_200_OK)
        improvements = self.client.get(f"/api/v1/ai/improvements/{workflow.pk}/")
        self.assertEqual(improvements.status_code, status.HTTP_200_OK)
        explanation = self.client.post(
            "/api/v1/ai/explain-failure/", {"execution_id": failed.pk}, format="json"
        )
        self.assertEqual(explanation.status_code, status.HTTP_200_OK)
        credentials_status = self.client.get(f"/api/v1/integrations/{integration.pk}/credentials-status/")
        self.assertEqual(credentials_status.status_code, status.HTTP_200_OK)
        self.assertFalse(credentials_status.data["configured"])

        logout = self.client.post("/api/v1/auth/logout/", {}, format="json")
        self.assertEqual(logout.status_code, status.HTTP_200_OK)
