from django.apps import AppConfig


class AuthConfig(AppConfig):
    name = 'apps.authentication'

    def ready(self):
        from . import signals  # noqa: F401
