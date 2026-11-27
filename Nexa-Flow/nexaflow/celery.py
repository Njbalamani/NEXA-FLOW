import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "nexaflow.settings")

app = Celery("nexaflow")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=False)
def debug_task(self):
    return {"task_id": self.request.id, "status": "ok"}
