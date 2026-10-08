import os
from pathlib import Path
from decimal import Decimal, InvalidOperation
from urllib.parse import unquote, urlsplit
import pymysql
from django.core.exceptions import ImproperlyConfigured

pymysql.install_as_MySQLdb()

BASE_DIR = Path(__file__).resolve().parent.parent
DEBUG = os.getenv("DJANGO_DEBUG", "0") == "1"
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-only-key-change-before-use")
if not DEBUG and (not os.getenv("DJANGO_SECRET_KEY") or SECRET_KEY == "dev-only-key-change-before-use"):
    raise ImproperlyConfigured("Définis DJANGO_SECRET_KEY avec une valeur longue et aléatoire en production.")
ALLOWED_HOSTS = [v.strip() for v in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if v.strip()]
CSRF_TRUSTED_ORIGINS = [v.strip() for v in os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if v.strip()]
if os.getenv("VERCEL", "") == "1":
    for hostname_variable in ("VERCEL_URL", "VERCEL_PROJECT_PRODUCTION_URL"):
        hostname = os.getenv(hostname_variable, "").strip().split("/", 1)[0]
        if hostname:
            ALLOWED_HOSTS.append(hostname)
            CSRF_TRUSTED_ORIGINS.append(f"https://{hostname}")
INSTALLED_APPS = ["django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes", "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles", "apps.core"]
MIDDLEWARE = ["django.middleware.security.SecurityMiddleware", "django.contrib.sessions.middleware.SessionMiddleware", "django.middleware.common.CommonMiddleware", "django.middleware.csrf.CsrfViewMiddleware", "django.contrib.auth.middleware.AuthenticationMiddleware", "django.contrib.messages.middleware.MessageMiddleware", "django.middleware.clickjacking.XFrameOptionsMiddleware"]
ROOT_URLCONF = "config.urls"
LOGIN_URL = "connexion"
LOGIN_REDIRECT_URL = "mon_compte"
LOGOUT_REDIRECT_URL = "accueil"
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [BASE_DIR / "templates"], "APP_DIRS": True, "OPTIONS": {"context_processors": ["django.template.context_processors.request", "django.contrib.auth.context_processors.auth", "django.contrib.messages.context_processors.messages", "apps.core.context_processors.hotel"]}}]
WSGI_APPLICATION = "config.wsgi.application"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
DATABASE_ENGINE = os.getenv("DATABASE_ENGINE", "mariadb").strip().lower()
if DATABASE_ENGINE == "sqlite":
    SQLITE_PATH = Path(os.getenv("SQLITE_PATH", str(BASE_DIR / "database" / "db.sqlite3")))
    SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": SQLITE_PATH, "OPTIONS": {"timeout": 20}}}
elif DATABASE_ENGINE in {"mariadb", "mysql"}:
    mysql_public_url = os.getenv("MYSQL_EXTERNAL_URL", os.getenv("MYSQL_PUBLIC_URL", "")).strip()
    if mysql_public_url:
        mysql_url = urlsplit(mysql_public_url)
    else:
        mysql_url = None
    if mysql_url and mysql_url.scheme in {"mysql", "mariadb"} and mysql_url.hostname:
        mysql_config = {
            "NAME": unquote(mysql_url.path.lstrip("/")) or os.getenv("MYSQLDATABASE", os.getenv("MYSQL_DATABASE", "naya_marina")),
            "USER": unquote(mysql_url.username or os.getenv("MYSQLUSER", os.getenv("MYSQL_USER", "naya_app"))),
            "PASSWORD": unquote(mysql_url.password or os.getenv("MYSQLPASSWORD", os.getenv("MYSQL_PASSWORD", ""))),
            "HOST": mysql_url.hostname,
            "PORT": str(mysql_url.port or os.getenv("MYSQLPORT", os.getenv("MYSQL_PORT", "3306"))),
        }
    else:
        mysql_config = {
            "NAME": os.getenv("MYSQL_DATABASE", os.getenv("MYSQLDATABASE", "naya_marina")),
            "USER": os.getenv("MYSQL_USER", os.getenv("MYSQLUSER", "naya_app")),
            "PASSWORD": os.getenv("MYSQL_PASSWORD", os.getenv("MYSQLPASSWORD", "")),
            "HOST": os.getenv("MYSQL_HOST", os.getenv("MYSQLHOST", "localhost")),
            "PORT": os.getenv("MYSQL_PORT", os.getenv("MYSQLPORT", "3306")),
        }
    DATABASES = {"default": {"ENGINE": "django.db.backends.mysql", **mysql_config, "OPTIONS": {"charset": "utf8mb4"}}}
else:
    raise ValueError("DATABASE_ENGINE doit être 'sqlite', 'mariadb' ou 'mysql'.")
LANGUAGE_CODE = "fr-fr"
TIME_ZONE = "Africa/Porto-Novo"
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
if os.getenv("DJANGO_USE_WHITENOISE", "0") == "1":
    MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")
    STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
    }
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
EMAIL_HOST = os.getenv("EMAIL_HOST", "")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "1") == "1"
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_TIMEOUT = int(os.getenv("EMAIL_TIMEOUT", "15"))
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "").strip() or EMAIL_HOST_USER or "noreply@hotel.local"
EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend" if EMAIL_HOST else "django.core.mail.backends.console.EmailBackend")
EMAIL_OUTBOX_ENCRYPTION_KEY = os.getenv("EMAIL_OUTBOX_ENCRYPTION_KEY", "").strip()
FEDAPAY_ENVIRONMENT = os.getenv("FEDAPAY_ENVIRONMENT", "sandbox").strip().lower()
if FEDAPAY_ENVIRONMENT != "sandbox":
    raise ImproperlyConfigured("Cette version est configurée uniquement pour FedaPay sandbox, y compris en déploiement.")
FEDAPAY_SECRET_KEY = os.getenv("FEDAPAY_SECRET_KEY", "").strip()
FEDAPAY_WEBHOOK_SECRET = os.getenv("FEDAPAY_WEBHOOK_SECRET", "").strip()
if FEDAPAY_SECRET_KEY and not FEDAPAY_SECRET_KEY.startswith("sk_sandbox_"):
    raise ImproperlyConfigured("FEDAPAY_SECRET_KEY doit être une clé secrète sandbox.")
FEDAPAY_ENABLED = bool(FEDAPAY_SECRET_KEY)
try:
    HOTEL_TAX_RATE = Decimal(os.getenv("HOTEL_TAX_RATE", "0"))
except InvalidOperation as exc:
    raise ImproperlyConfigured("HOTEL_TAX_RATE doit être un nombre entre 0 et 100.") from exc
if not HOTEL_TAX_RATE.is_finite() or not 0 <= HOTEL_TAX_RATE <= 100 or HOTEL_TAX_RATE.quantize(Decimal("0.01")) != HOTEL_TAX_RATE:
    raise ImproperlyConfigured("HOTEL_TAX_RATE doit être un nombre entre 0 et 100.")
try:
    HOTEL_CANCELLATION_NOTICE_DAYS = int(os.getenv("HOTEL_CANCELLATION_NOTICE_DAYS", "2"))
except ValueError as exc:
    raise ImproperlyConfigured("HOTEL_CANCELLATION_NOTICE_DAYS doit être un entier entre 0 et 365.") from exc
if not 0 <= HOTEL_CANCELLATION_NOTICE_DAYS <= 365:
    raise ImproperlyConfigured("HOTEL_CANCELLATION_NOTICE_DAYS doit être un entier entre 0 et 365.")

SECURE_SSL_REDIRECT = os.getenv("DJANGO_SECURE_SSL_REDIRECT", "0" if DEBUG else "1") == "1"
SESSION_COOKIE_SECURE = os.getenv("DJANGO_SESSION_COOKIE_SECURE", "0" if DEBUG else "1") == "1"
CSRF_COOKIE_SECURE = os.getenv("DJANGO_CSRF_COOKIE_SECURE", "0" if DEBUG else "1") == "1"
SECURE_HSTS_SECONDS = int(os.getenv("DJANGO_SECURE_HSTS_SECONDS", "0" if DEBUG else "31536000"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = os.getenv("DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", "0") == "1"
SECURE_HSTS_PRELOAD = os.getenv("DJANGO_SECURE_HSTS_PRELOAD", "0") == "1"
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
SECURE_REFERRER_POLICY = "same-origin"
if os.getenv("DJANGO_SECURE_PROXY_SSL_HEADER", "0") == "1":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
elif os.getenv("VERCEL", "") == "1":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
