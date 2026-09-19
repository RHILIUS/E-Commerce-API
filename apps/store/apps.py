from django.apps import AppConfig


class StoreConfig(AppConfig):
    name = 'apps.store'

    def ready(self):
        from . import signals  # noqa: F401
