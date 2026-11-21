"""Django settings for the GoTani project.

Environment-driven via `.env` (python-dotenv) and `DATABASE_URL` (dj-database-url).
Falls back to a local SQLite database when `DATABASE_URL` is not set.
"""

import os
import sys
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


DEBUG = env_bool("DEBUG", default=True)

SECRET_KEY = os.environ.get("SECRET_KEY") or (
    "django-insecure-dev-only-key-change-me" if DEBUG else None
)
if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY must be set when DEBUG is False.")

ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    # Dev: WhiteNoise serves /static/ under runserver too, with max-age=0 while DEBUG, so the browser always
    # revalidates and an edited script or stylesheet shows up on the next load (D112).
    "whitenoise.runserver_nostatic",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "core",
    "farm",
    "inventory",
    "pos",
    "reports",
    "invoicing",
    "geo",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "core.middleware.UserLanguageMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "core.middleware.ForcePasswordChangeMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.template.context_processors.i18n",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.tax",
                "farm.access.roles_context",
                "core.nav.nav_context",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=600,
        conn_health_checks=True,
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "id"
# Per-user language (Phase 7): core.middleware.UserLanguageMiddleware picks the user's profile language,
# else the language cookie, else LANGUAGE_CODE — never the browser's Accept-Language.
LANGUAGES = [("id", "Bahasa Indonesia"), ("en", "English")]
LOCALE_PATHS = [BASE_DIR / "locale"]
# App date formats per language (D119): templates use the named formats, e.g. |date:"SHORT_DATE_FORMAT".
FORMAT_MODULE_PATH = ["core.formats"]
TIME_ZONE = "Asia/Jakarta"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    # Manifest storage needs `collectstatic`; keep plain storage for dev and tests.
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        if DEBUG
        else "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# External data cache (D73): weather 30 min, NDVI/soil 24 h. Table made by `createcachetable`.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "gotani_cache",
    }
}

# Agromonitoring free-tier key (server-side only, never sent to the browser). Optional.
AGRO_API_KEY = os.environ.get("AGRO_API_KEY", "")

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"

if len(sys.argv) > 1 and sys.argv[1] == "test":
    # Fast hashing for tests only (fixtures create many users).
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Errors (and gunicorn-served tracebacks) go to stdout -> journald on the VM.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {
        "django": {"handlers": ["console"], "level": os.environ.get("DJANGO_LOG_LEVEL", "INFO"), "propagate": False},
    },
}

if not DEBUG:
    # Caddy terminates TLS (D33) and forwards X-Forwarded-Proto.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", default=True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = int(os.environ.get("SECURE_HSTS_SECONDS", "3600"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    # A *.duckdns.org subdomain cannot be submitted to the HSTS preload list (D39).
    SILENCED_SYSTEM_CHECKS = ["security.W021"]
