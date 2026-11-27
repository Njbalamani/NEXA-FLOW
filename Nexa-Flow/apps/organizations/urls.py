from django.urls import path

from . import views

urlpatterns = [
    path("settings/", views.organization_settings, name="organization-settings"),
    path("switch/<int:organization_id>/", views.switch_organization, name="switch-organization"),
]
