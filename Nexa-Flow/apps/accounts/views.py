from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.contrib import messages

from apps.core.utils import safe_redirect
from apps.organizations.services import create_personal_organization

from .forms import LoginForm, RegistrationForm


def register(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    if request.method == "POST":
        form = RegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            organization = create_personal_organization(user)
            user.active_organization = organization
            user.save(update_fields=["active_organization"])
            login(request, user)
            messages.success(request, "Welcome to NexaFlow. Your workspace is ready.")
            return redirect("dashboard")
    else:
        form = RegistrationForm()
    return render(request, "accounts/register.html", {"form": form})


def login_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    if request.method == "POST":
        form = LoginForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            return redirect(safe_redirect(request.GET.get("next")))
    else:
        form = LoginForm(request)
    return render(request, "accounts/login.html", {"form": form})


def logout_view(request):
    logout(request)
    return redirect("landing")


@login_required
def profile(request):
    if request.method == "POST":
        user = request.user
        user.full_name = request.POST.get("full_name", "").strip()
        user.phone = request.POST.get("phone", "").strip()
        user.timezone = request.POST.get("timezone", "UTC").strip() or "UTC"
        user.save(update_fields=["full_name", "phone", "timezone", "updated_at"])
        messages.success(request, "Profile updated.")
        return redirect("profile")
    return render(request, "accounts/profile.html")
