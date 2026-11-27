from django.urls import path

from . import views

urlpatterns = [
    path("", views.task_list, name="task-list"),
    path("create/", views.task_create, name="task-create"),
    path("<int:task_id>/status/", views.task_status, name="task-status"),
]
