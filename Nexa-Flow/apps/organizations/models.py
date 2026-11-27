from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.utils import unique_slug

ROLE_CHOICES = [
    ("owner", "Owner"),
    ("admin", "Admin"),
    ("manager", "Manager"),
    ("member", "Member"),
    ("viewer", "Viewer"),
]


class Organization(models.Model):
    name = models.CharField(max_length=180)
    slug = models.SlugField(max_length=200, unique=True)
    description = models.TextField(blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="owned_organizations")
    logo = models.ImageField(upload_to="organization-logos/", blank=True, null=True)
    website = models.URLField(blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slug(Organization, self.name)
        super().save(*args, **kwargs)

    @property
    def member_count(self):
        return self.members.filter(is_active=True).count()


class OrganizationMember(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="members")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="organization_memberships")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default="member")
    title = models.CharField(max_length=120, blank=True)
    is_active = models.BooleanField(default=True)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="invited_members"
    )
    joined_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["organization", "user"], name="uniq_org_member")]
        ordering = ["-joined_at"]

    def __str__(self):
        return f"{self.user.email} · {self.organization.name} ({self.role})"


class Workspace(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="workspaces")
    name = models.CharField(max_length=160)
    slug = models.SlugField(max_length=180)
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="created_workspaces")
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["organization", "slug"], name="uniq_workspace_org_slug")]
        ordering = ["name"]

    def __str__(self):
        return f"{self.organization.name} / {self.name}"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slug(Workspace, self.name, {"organization_id": self.organization_id})
        super().save(*args, **kwargs)


class Team(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="teams")
    workspace = models.ForeignKey(Workspace, on_delete=models.SET_NULL, null=True, blank=True, related_name="teams")
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL, through="TeamMembership", related_name="teams", blank=True
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["organization", "name"], name="uniq_team_org_name")]
        ordering = ["name"]

    def __str__(self):
        return self.name


class TeamMembership(models.Model):
    TEAM_ROLES = (("lead", "Lead"), ("member", "Member"))

    team = models.ForeignKey(Team, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    role = models.CharField(max_length=20, choices=TEAM_ROLES, default="member")
    joined_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["team", "user"], name="uniq_team_member")]
