INSTALLED_APPS = [
    "django.contrib.auth",
    "rest_framework",
    "api",
]

MIDDLEWARE = [
    "django.contrib.auth.middleware.AuthenticationMiddleware",
]

ROOT_URLCONF = "config.urls"

REST_FRAMEWORK = {
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
}
