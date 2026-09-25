"""Role definitions. Roles are Django Groups; a superuser passes every role check."""

from django.apps import apps
from django.contrib.auth.management import create_permissions
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.management import create_contenttypes

MODERATOR = "Moderator"
EVENT_ADMIN = "EventAdmin"

# (app_label, codename) grants for the Django admin. The REST API authorises by group
# name (see ``accounts.permissions``); these grants only control what each role sees in
# /admin/.
ROLE_PERMISSIONS = {
    MODERATOR: [
        ("nominations", "view_nomination"),
        ("nominations", "change_nomination"),
        ("nominations", "view_nominee"),
        ("events", "view_event"),
        ("events", "view_category"),
        ("events", "view_award"),
    ],
    EVENT_ADMIN: [
        (app, f"{action}_{model}")
        for app, model in [("events", "event"), ("events", "category"), ("events", "award")]
        for action in ("view", "add", "change", "delete")
    ]
    + [
        ("nominations", "view_nomination"),
        ("nominations", "view_nominee"),
        ("voting", "view_vote"),
        ("voting", "void_vote"),
        ("audit", "view_auditlog"),
    ],
}


def ensure_roles(using="default"):
    """Create the role groups and attach their permissions. Safe to call repeatedly."""
    # Permissions are normally created by a post_migrate handler per app, in app order;
    # create them for every app now so this works whichever handler runs first.
    for app_config in apps.get_app_configs():
        create_contenttypes(app_config, verbosity=0, using=using)
        create_permissions(app_config, verbosity=0, using=using)
    groups = {}
    for name, grants in ROLE_PERMISSIONS.items():
        group, _ = Group.objects.using(using).get_or_create(name=name)
        permissions = [
            permission
            for app_label, codename in grants
            for permission in Permission.objects.using(using).filter(
                content_type__app_label=app_label, codename=codename
            )
        ]
        group.permissions.set(permissions)
        groups[name] = group
    return groups


def user_has_role(user, role):
    """Active staff user who is a superuser or a member of the ``role`` group."""
    if not (user and user.is_authenticated and user.is_active and getattr(user, "is_staff", False)):
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name=role).exists()
