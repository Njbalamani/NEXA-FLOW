from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import User
from apps.organizations.models import Organization
from apps.workflows.services import create_workflow_from_definition


TEMPLATES = {
    "Approval task router": {
        "name": "Approval task router",
        "description": "Routes high-value requests to an approver and records the decision.",
        "status": "draft",
        "nodes": [
            {"key": "start", "type": "trigger", "name": "Request received", "config": {}, "position": {"x": 80, "y": 180}},
            {
                "key": "check_amount",
                "type": "condition",
                "name": "High value?",
                "config": {"field": "trigger.payload.amount", "operator": "gte", "value": 100},
                "position": {"x": 320, "y": 180},
            },
            {
                "key": "create_review",
                "type": "action",
                "name": "Create review task",
                "config": {"action": "create_task", "title": "Review request", "priority": "high"},
                "position": {"x": 580, "y": 100},
            },
            {
                "key": "log_approved",
                "type": "action",
                "name": "Log routine request",
                "config": {"action": "log", "message": "Routine request recorded"},
                "position": {"x": 580, "y": 300},
            },
        ],
        "connections": [
            {"source": "start", "target": "check_amount", "source_handle": "out"},
            {"source": "check_amount", "target": "create_review", "source_handle": "true"},
            {"source": "check_amount", "target": "log_approved", "source_handle": "false"},
        ],
        "triggers": [{"name": "Manual", "type": "manual", "config": {}, "is_active": True}],
    },
    "Daily operations digest": {
        "name": "Daily operations digest",
        "description": "Creates a daily digest task and writes an audit log entry.",
        "status": "draft",
        "nodes": [
            {"key": "midnight", "type": "trigger", "name": "Daily", "config": {}, "position": {"x": 80, "y": 160}},
            {
                "key": "digest",
                "type": "action",
                "name": "Create digest task",
                "config": {"action": "create_task", "title": "Review daily operations", "priority": "medium"},
                "position": {"x": 340, "y": 160},
            },
        ],
        "connections": [{"source": "midnight", "target": "digest", "source_handle": "out"}],
        "triggers": [
            {
                "name": "Every day",
                "type": "schedule",
                "config": {"mode": "interval", "minutes": 1440},
                "is_active": True,
            }
        ],
    },
    "Inbound webhook notifier": {
        "name": "Inbound webhook notifier",
        "description": "Turns a signed webhook event into an in-app notification.",
        "status": "draft",
        "nodes": [
            {"key": "webhook_start", "type": "trigger", "name": "Webhook", "config": {}, "position": {"x": 80, "y": 160}},
            {
                "key": "notify_team",
                "type": "action",
                "name": "Notify team",
                "config": {"action": "notification", "title": "New webhook event", "body": "An external system sent an event."},
                "position": {"x": 340, "y": 160},
            },
        ],
        "connections": [{"source": "webhook_start", "target": "notify_team", "source_handle": "out"}],
        "triggers": [{"name": "Signed webhook", "type": "webhook", "config": {}, "is_active": True}],
    },
}


class Command(BaseCommand):
    help = "Seed the active organization with a small gallery of reusable workflow templates."

    def add_arguments(self, parser):
        parser.add_argument("--organization", type=int, help="Organization ID")
        parser.add_argument("--user", help="Email of a user whose active organization should receive templates")

    def handle(self, *args, **options):
        organization = None
        user = None
        if options["organization"]:
            organization = Organization.objects.filter(pk=options["organization"]).first()
        elif options["user"]:
            user = User.objects.filter(email__iexact=options["user"]).first()
            organization = user.active_organization if user else None
        else:
            user = User.objects.filter(active_organization__isnull=False).first()
            organization = user.active_organization if user else None
        if not organization:
            raise CommandError("No organization found. Pass --organization or --user.")

        created = 0
        for name, definition in TEMPLATES.items():
            if organization.workflows.filter(name=name, is_template=True).exists():
                self.stdout.write(f"Skipping existing template: {name}")
                continue
            create_workflow_from_definition(organization, definition, user=user, as_template=True)
            created += 1
            self.stdout.write(self.style.SUCCESS(f"Created template: {name}"))
        self.stdout.write(self.style.SUCCESS(f"Seeded {created} templates into {organization.name}."))
