from django.apps import AppConfig


class BillingConfig(AppConfig):
    name = "examples.billing"
    label = "billing"

    def ready(self):
        # Import actions here so they are registered exactly once at startup.
        import examples.billing.actions  # noqa: F401
