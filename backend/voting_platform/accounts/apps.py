from django.apps import AppConfig
from django.db.models.signals import post_migrate


def _ensure_roles(sender, using="default", **kwargs):
    from accounts.roles import ensure_roles

    ensure_roles(using=using)


class AccountsConfig(AppConfig):
    name = "accounts"

    def ready(self):
        # Create the Moderator / EventAdmin groups and their Django permissions after
        # every migrate (idempotent). Connected once, for this app's signal only.
        post_migrate.connect(_ensure_roles, sender=self, dispatch_uid="accounts.ensure_roles")
