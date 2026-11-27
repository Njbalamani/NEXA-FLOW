from urllib.parse import urlparse

from django.utils.text import slugify


def unique_slug(model, name: str, extra_filters: dict | None = None) -> str:
    base = slugify(name) or "workspace"
    slug = base
    counter = 2
    filters = dict(extra_filters or {})
    while model.objects.filter(slug=slug, **filters).exists():
        slug = f"{base}-{counter}"
        counter += 1
    return slug


def safe_redirect(target: str | None, fallback: str = "dashboard") -> str:
    if not target:
        return fallback
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc or not target.startswith("/"):
        return fallback
    return target
