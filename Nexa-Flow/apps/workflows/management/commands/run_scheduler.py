from django.core.management.base import BaseCommand

from apps.workflows.tasks import run_due_workflows


class Command(BaseCommand):
    help = "Enqueue all due scheduled workflow triggers."

    def handle(self, *args, **options):
        result = run_due_workflows()
        self.stdout.write(
            self.style.SUCCESS(
                f"Checked schedules at {result['checked_at']}; enqueued {result['scheduled']} execution(s)."
            )
        )
