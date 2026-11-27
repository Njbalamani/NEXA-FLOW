from django.urls import path

from . import views

urlpatterns = [
    path("", views.integration_list, name="integration-list"),
    path("<int:integration_id>/delete/", views.integration_delete, name="integration-delete"),
    path("<int:integration_id>/test/", views.integration_test, name="integration-test"),
]
