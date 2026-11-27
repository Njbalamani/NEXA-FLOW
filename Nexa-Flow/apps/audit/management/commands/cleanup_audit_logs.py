from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.audit.models import AuditLog


class Command(BaseCommand):
    help = "Delete audit log entries older than a retention window."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=90)
        parser.add_argument("--organization", type=int)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        days = options["days"]
        if days < 1:
            raise CommandError("--days must be at least 1.")

        cutoff = timezone.now() - timedelta(days=days)
        queryset = AuditLog.objects.filter(created_at__lt=cutoff)
        if options["organization"]:
            queryset = queryset.filter(organization_id=options["organization"])
        count = queryset.count()

        if options["dry_run"]:
            self.stdout.write(f"{count} audit log entries would be deleted.")
            return

        deleted, _ = queryset.delete()
        self.stdout.write(self.style.SUCCESS(f"Deleted {deleted} audit log entries older than {days} days."))
