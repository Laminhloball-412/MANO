import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("UE_PORTAL_SECRET_KEY", "ansa-ue-portal-development-key")
DEBUG = os.environ.get("UE_PORTAL_DEBUG", "1") == "1"
ALLOWED_HOSTS = [item.strip() for item in os.environ.get("UE_PORTAL_ALLOWED_HOSTS", "*").split(",") if item.strip()]

INSTALLED_APPS = [
    "django.contrib.staticfiles",
    "portal",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "ue_service_portal.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    }
]

WSGI_APPLICATION = "ue_service_portal.wsgi.application"
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Ho_Chi_Minh"
USE_I18N = True
USE_L10N = True
USE_TZ = True
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
DEFAULT_AUTO_FIELD = "django.db.models.AutoField"

UE_CONFIG_DIR = Path(os.environ.get("UE_CONFIG_DIR", "/home/ubuntu/ran/srsRAN_4G/build/srsue/src"))
UE_DRAFT_REGISTRY = Path(os.environ.get("UE_DRAFT_REGISTRY", str(BASE_DIR / "data" / "ue_drafts.json")))
UE_PROVISIONING_ENABLED = os.environ.get("UE_PROVISIONING_ENABLED", "0") == "1"
WORKLOAD_RUNNER_ENABLED = os.environ.get("WORKLOAD_RUNNER_ENABLED", "0") == "1"
UE_PORTAL_ROOT = BASE_DIR
UE_RUNTIME_DIR = Path(os.environ.get("UE_RUNTIME_DIR", str(BASE_DIR / "runtime")))
RAN_ROOT = Path(os.environ.get("RAN_ROOT", "/home/ubuntu/ran"))
GNB_DIR = Path(os.environ.get("GNB_DIR", str(RAN_ROOT / "srsRAN_project" / "build" / "apps" / "gnb")))
GNB_CONFIG = Path(os.environ.get("GNB_CONFIG", str(GNB_DIR / "gnb1.yaml")))
_gnb_slicing_config = os.environ.get("GNB_SLICING_CONFIG", "").strip()
GNB_SLICING_CONFIG = Path(_gnb_slicing_config) if _gnb_slicing_config else None
UE_BINARY = Path(os.environ.get("UE_BINARY", str(UE_CONFIG_DIR / "srsue")))
PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://127.0.0.1:30090")
PORTAL_DATA_URL = os.environ.get("PORTAL_DATA_URL", "http://192.168.180.35:8890")
