from django.urls import path

from . import views

urlpatterns = [
    path("", views.workflow_list, name="workflow-list"),
    path("create/", views.workflow_create, name="workflow-create"),
    path("<int:workflow_id>/", views.workflow_builder, name="workflow-builder"),
    path("<int:workflow_id>/delete/", views.workflow_delete, name="workflow-delete"),
    path("<int:workflow_id>/execute/", views.workflow_execute, name="workflow-execute"),
    path("executions/", views.execution_list, name="execution-list"),
    path("executions/<int:execution_id>/", views.execution_detail, name="execution-detail"),
    path("executions/<int:execution_id>/cancel/", views.execution_cancel, name="execution-cancel"),
    path("executions/<int:execution_id>/retry/", views.execution_retry, name="execution-retry"),
]
