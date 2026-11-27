from django.contrib import admin

from .models import Task, TaskEvent

admin.site.register(Task)
admin.site.register(TaskEvent)
