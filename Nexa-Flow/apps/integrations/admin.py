from django.contrib import admin

from .models import Integration, WebhookDelivery

admin.site.register(Integration)
admin.site.register(WebhookDelivery)
