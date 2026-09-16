from django.apps import AppConfig


class EcommerceConfig(AppConfig):
    name = 'ecommerce'

    def ready(self):
        from . import signals  # noqa: F401
