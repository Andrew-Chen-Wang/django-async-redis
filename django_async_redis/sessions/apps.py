from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class SessionsConfig(AppConfig):
    name = "django_async_redis.sessions"
    verbose_name = _("Sessions")
