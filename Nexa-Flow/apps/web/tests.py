from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core.utils import safe_redirect
from apps.notifications.models import Notification
from apps.organizations.services import create_personal_organization
from apps.tasks.models import Task
from apps.workflows.models import Workflow, WorkflowExecution
from apps.workflows.services import save_workflow_definition

User = get_user_model()


class WebSmokeTests(TestCase):
    def test_landing_page_renders(self):
        response = self.client.get(reverse("landing"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Automate the work")

    def test_security_headers_are_present(self):
        response = self.client.get(reverse("landing"))
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response["Referrer-Policy"], "strict-origin-when-cross-origin")
        self.assertEqual(response["Cross-Origin-Opener-Policy"], "same-origin")
        self.assertIn("geolocation=()", response["Permissions-Policy"])

    def test_health_endpoint_reports_database(self):
        response = self.client.get("/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertEqual(response.json()["database"], "ok")
        self.assertEqual(response.json()["cache"], "ok")
        self.assertIn(response.json()["celery"], {"eager", "configured"})
        self.assertEqual(response.json()["service"], "nexaflow")

    def test_request_id_header_is_echoed_and_generated(self):
        supplied = self.client.get(reverse("landing"), HTTP_X_REQUEST_ID="trace-123")
        self.assertEqual(supplied["X-Request-ID"], "trace-123")

        generated = self.client.get(reverse("landing"))
        self.assertTrue(generated["X-Request-ID"])

    def test_safe_redirect_rejects_external_targets(self):
        self.assertEqual(safe_redirect("/dashboard/"), "/dashboard/")
        self.assertEqual(safe_redirect("https://evil.example"), "dashboard")
        self.assertEqual(safe_redirect("//evil.example"), "dashboard")

    def test_web_registration_creates_organization(self):
        response = self.client.post(
            reverse("register"),
            {
                "full_name": "Web Owner",
                "email": "webowner@example.com",
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard"))
        user = User.objects.get(email="webowner@example.com")
        self.assertTrue(user.organization_memberships.exists())

    def test_dashboard_renders_real_metrics(self):
        user = User.objects.create_user(email="dash@example.com", password="StrongPass123!", full_name="Dash User")
        organization = create_personal_organization(user)
        user.active_organization = organization
        user.save(update_fields=["active_organization"])
        workflow = Workflow.objects.create(organization=organization, name="Dashboard workflow", created_by=user)
        save_workflow_definition(
            workflow,
            {
                "name": "Dashboard workflow",
                "status": "active",
                "nodes": [{"key": "trigger_1", "type": "trigger", "name": "Start", "config": {}}],
                "connections": [],
                "triggers": [{"name": "Manual", "type": "manual", "config": {}}],
            },
            user,
        )
        self.client.force_login(user)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dashboard workflow")
        self.assertContains(response, "data-chart=\"activity\"")

    def test_management_pages_render(self):
        user = User.objects.create_user(email="pages@example.com", password="StrongPass123!", full_name="Pages")
        organization = create_personal_organization(user)
        user.active_organization = organization
        user.save(update_fields=["active_organization"])
        self.client.force_login(user)
        for url_name in (
            "billing",
            "organization-settings",
            "integration-list",
            "notification-list",
            "task-list",
            "workflow-list",
            "execution-list",
            "ai-assistant",
            "profile",
        ):
            response = self.client.get(reverse(url_name))
            self.assertEqual(response.status_code, 200, url_name)

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True)
    def test_builder_page_renders(self):
        user = User.objects.create_user(email="builder@example.com", password="StrongPass123!", full_name="Builder")
        organization = create_personal_organization(user)
        user.active_organization = organization
        user.save(update_fields=["active_organization"])
        workflow = Workflow.objects.create(organization=organization, name="Builder test", created_by=user)
        self.client.force_login(user)
        response = self.client.get(reverse("workflow-builder", args=[workflow.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Builder test")
        self.assertContains(response, "builder-root")
        self.assertContains(response, "workflow3d")

    @override_settings(CELERY_TASK_ALWAYS_EAGER=True, CELERY_TASK_EAGER_PROPAGATES=True)
    def test_execution_cancel_and_retry_actions(self):
        user = User.objects.create_user(email="executions@example.com", password="StrongPass123!", full_name="Runs")
        organization = create_personal_organization(user)
        user.active_organization = organization
        user.save(update_fields=["active_organization"])
        workflow = Workflow.objects.create(organization=organization, name="Actions", created_by=user)
        save_workflow_definition(
            workflow,
            {
                "name": "Actions",
                "status": "active",
                "nodes": [{"key": "trigger_1", "type": "trigger", "name": "Start", "config": {}}],
                "connections": [],
                "triggers": [{"name": "Manual", "type": "manual", "config": {}}],
            },
            user,
        )
        queued = WorkflowExecution.objects.create(workflow=workflow, status="queued", triggered_by=user)
        self.client.force_login(user)

        cancelled = self.client.post(reverse("execution-cancel", args=[queued.pk]))
        self.assertEqual(cancelled.status_code, 302)
        queued.refresh_from_db()
        self.assertEqual(queued.status, "cancelled")

        failed = WorkflowExecution.objects.create(workflow=workflow, status="failed", input={"amount": 5})
        retried = self.client.post(reverse("execution-retry", args=[failed.pk]))
        self.assertEqual(retried.status_code, 302)
        new_execution = WorkflowExecution.objects.order_by("-pk").first()
        self.assertEqual(retried.url, reverse("execution-detail", args=[new_execution.pk]))
        self.assertEqual(new_execution.input, {"amount": 5})

    def test_workflow_creation_requires_post(self):
        user = User.objects.create_user(email="create@example.com", password="StrongPass123!", full_name="Create")
        organization = create_personal_organization(user)
        user.active_organization = organization
        user.save(update_fields=["active_organization"])
        self.client.force_login(user)

        self.client.get(reverse("workflow-create"))
        self.assertEqual(Workflow.objects.filter(organization=organization).count(), 0)

        self.client.post(reverse("workflow-create"), {"name": "From form"})
        self.assertEqual(Workflow.objects.filter(organization=organization).count(), 1)

    def test_task_assignee_must_be_active_organization_member(self):
        user = User.objects.create_user(email="assign@example.com", password="StrongPass123!", full_name="Assign")
        organization = create_personal_organization(user)
        user.active_organization = organization
        user.save(update_fields=["active_organization"])
        outsider = User.objects.create_user(email="outsider@example.com", password="StrongPass123!")
        self.client.force_login(user)

        self.client.post(reverse("task-create"), {"title": "Foreign assignee", "assignee": outsider.pk})
        self.assertFalse(Task.objects.filter(organization=organization, title="Foreign assignee").exists())

        self.client.post(reverse("task-create"), {"title": "Valid assignee", "assignee": user.pk})
        task = Task.objects.get(organization=organization, title="Valid assignee")
        self.assertEqual(task.assignee, user)

    def test_notification_mark_read_is_scoped_to_visible_inbox(self):
        user = User.objects.create_user(email="inbox@example.com", password="StrongPass123!", full_name="Inbox")
        organization = create_personal_organization(user)
        user.active_organization = organization
        user.save(update_fields=["active_organization"])
        other = User.objects.create_user(email="other-inbox@example.com", password="StrongPass123!")
        own = Notification.objects.create(organization=organization, user=user, title="Own notice")
        foreign = Notification.objects.create(organization=organization, user=other, title="Other notice")
        self.client.force_login(user)

        response = self.client.post(reverse("notification-list"), {"ids": [own.pk, foreign.pk]})
        self.assertEqual(response.status_code, 302)
        own.refresh_from_db()
        foreign.refresh_from_db()
        self.assertIsNotNone(own.read_at)
        self.assertIsNone(foreign.read_at)
