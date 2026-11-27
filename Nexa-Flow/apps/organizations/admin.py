from django.contrib import admin

from .models import Organization, OrganizationMember, Team, TeamMembership, Workspace

admin.site.register(Organization)
admin.site.register(OrganizationMember)
admin.site.register(Workspace)
admin.site.register(Team)
admin.site.register(TeamMembership)
