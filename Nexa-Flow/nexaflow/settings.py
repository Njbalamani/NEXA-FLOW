"""Settings for the NexaFlow enterprise workflow platform."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from django.core.exceptions import ImproperlyConfigured
from django.core.management.utils import get_random_secret_key

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - optional local convenience
    pass


BASE_DIR = Path(__file__).resolve().parent.parent


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in env(name, default).split(",") if item.strip()]


DEBUG = env_bool("DEBUG", True)
SECRET_KEY = env("DJANGO_SECRET_KEY") or (get_random_secret_key() if DEBUG else "")
if not SECRET_KEY:
    raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set when DEBUG is false.")
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1,testserver")
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework.authtoken",
    "apps.core",
    "apps.accounts",
    "apps.organizations",
    "apps.workflows",
    "apps.tasks",
    "apps.integrations",
    "apps.notifications",
    "apps.billing",
    "apps.audit",
    "apps.ai",
    "apps.analytics",
    "apps.api",
    "apps.web",
    "apps.health",
]

if importlib.util.find_spec("channels"):
    INSTALLED_APPS.append("channels")

HAS_SPECTACULAR = bool(importlib.util.find_spec("drf_spectacular"))
if HAS_SPECTACULAR:
    INSTALLED_APPS.append("drf_spectacular")

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "apps.core.middleware.SecurityHeadersMiddleware",
    "apps.core.middleware.RequestIdMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.RequestContextMiddleware",
]

ROOT_URLCONF = "nexaflow.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context.organization_context",
            ],
        },
    },
]

WSGI_APPLICATION = "nexaflow.wsgi.application"
ASGI_APPLICATION = "nexaflow.asgi.application"

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}


def database_config() -> dict:
    raw = env("DATABASE_URL", "")
    if not raw:
        return {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / env("SQLITE_NAME", "db.sqlite3"),
        }

    if raw.startswith("sqlite://"):
        return {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / (unquote(urlparse(raw).path).lstrip("/") or "db.sqlite3"),
        }

    parsed = urlparse(raw.replace("postgres://", "postgresql://", 1))
    query = parse_qs(parsed.query)
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": unquote(parsed.path).lstrip("/"),
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname or "localhost",
        "PORT": str(parsed.port or 5432),
        "OPTIONS": {key: value[0] for key, value in query.items() if len(value) == 1},
    }


DATABASES = {"default": database_config()}

AUTH_USER_MODEL = "accounts.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = env("TIME_ZONE", "UTC")
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.TokenAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_THROTTLE_CLASSES": ["rest_framework.throttling.ScopedRateThrottle"],
    "DEFAULT_THROTTLE_RATES": {
        "auth": env("RATE_LIMIT_AUTH", "10/min"),
        "api": env("RATE_LIMIT_API", "120/min"),
        "webhook": env("RATE_LIMIT_WEBHOOK", "300/min"),
        "ai": env("RATE_LIMIT_AI", "20/min"),
        "billing": env("RATE_LIMIT_BILLING", "20/min"),
    },
    "DEFAULT_FILTER_BACKENDS": [
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_PAGINATION_CLASS": "apps.api.pagination.NexaPagination",
    "PAGE_SIZE": 25,
    "EXCEPTION_HANDLER": "apps.api.exceptions.api_exception_handler",
}
if HAS_SPECTACULAR:
    REST_FRAMEWORK["DEFAULT_SCHEMA_CLASS"] = "drf_spectacular.openapi.AutoSchema"
    SPECTACULAR_SETTINGS = {
        "TITLE": "NexaFlow API",
        "DESCRIPTION": "AI-powered workflow automation platform API.",
        "VERSION": "1.0.0",
        "SERVE_INCLUDE_SCHEMA": False,
        "COMPONENT_SPLIT_REQUEST": True,
        "SECURITY": [{"TokenAuth": []}],
        "SECURITY_SCHEMES": {
            "TokenAuth": {"type": "apiKey", "in": "header", "name": "Authorization", "description": "Token <key>"}
        },
    }

CELERY_BROKER_URL = env("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")
CELERY_TASK_ALWAYS_EAGER = env_bool("CELERY_TASK_ALWAYS_EAGER", False)
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULE = {
    "run-due-workflows-every-minute": {
        "task": "apps.workflows.tasks.run_due_workflows",
        "schedule": 60.0,
    },
}

NEXAFLOW = {
    "ENCRYPTION_KEY": env("NEXAFLOW_ENCRYPTION_KEY", ""),
    "AI_API_KEY": env("AI_API_KEY", ""),
    "AI_API_URL": env("AI_API_URL", "https://api.openai.com/v1/chat/completions"),
    "AI_MODEL": env("AI_MODEL", "gpt-4o-mini"),
    "AI_TIMEOUT_SECONDS": float(env("AI_TIMEOUT_SECONDS", "12")),
    "MAX_NODES_PER_WORKFLOW": int(env("MAX_NODES_PER_WORKFLOW", "50")),
    "MAX_EXECUTION_SECONDS": int(env("MAX_EXECUTION_SECONDS", "60")),
    "WEBHOOK_MAX_BODY_BYTES": int(env("WEBHOOK_MAX_BODY_BYTES", "262144")),
    "EMAIL_FROM": env("EMAIL_FROM", "no-reply@nexaflow.local"),
    "FRONTEND_URL": env("FRONTEND_URL", "http://localhost:8000"),
    "ALLOWED_ENV_HEADERS": env_list("ALLOWED_ENV_HEADERS", ""),
    "WEBHOOK_REPLAY_TTL_SECONDS": int(env("WEBHOOK_REPLAY_TTL_SECONDS", "300")),
    "WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS": int(env("WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS", "300")),
    "WEBHOOK_RATE_LIMIT_PER_MINUTE": int(env("WEBHOOK_RATE_LIMIT_PER_MINUTE", "300")),
    "TRUST_X_FORWARDED_FOR": env_bool("TRUST_X_FORWARDED_FOR", False),
}

CACHE_URL = env("CACHE_URL", "")
if CACHE_URL.startswith("redis://"):
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": CACHE_URL,
        }
    }
else:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "nexaflow"}}

EMAIL_BACKEND = env(
    "EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend" if DEBUG else "django.core.mail.backends.smtp.EmailBackend"
)
EMAIL_HOST = env("EMAIL_HOST", "localhost")
EMAIL_PORT = int(env("EMAIL_PORT", "1025"))
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", False)
DEFAULT_FROM_EMAIL = NEXAFLOW["EMAIL_FROM"]

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"standard": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "standard"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
}

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = int(env("SECURE_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    X_FRAME_OPTIONS = "DENY"
