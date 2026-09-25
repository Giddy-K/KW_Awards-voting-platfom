from rest_framework.permissions import SAFE_METHODS, BasePermission

from accounts.models import User
from accounts.roles import EVENT_ADMIN, MODERATOR, user_has_role


class RolePermission(BasePermission):
    """Group-based role check (superuser passes). Denials by authenticated staff are audited."""

    role = None
    message = "You do not have permission to perform this action."

    def has_permission(self, request, view):
        user = request.user
        allowed = user_has_role(user, self.role)
        if not allowed and isinstance(user, User) and user.is_authenticated:
            from audit import services as audit
            from audit.models import AuditAction

            audit.log(
                AuditAction.PERMISSION_DENIED,
                request=request,
                metadata={
                    "path": request.path,
                    "method": request.method,
                    "required_role": self.role,
                },
            )
        return allowed


class IsModerator(RolePermission):
    role = MODERATOR


class IsEventAdmin(RolePermission):
    role = EVENT_ADMIN


class IsEventAdminOrReadOnly(IsEventAdmin):
    """Anyone may read; only event admins may write."""

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        return super().has_permission(request, view)


class HasAjaxHeader(BasePermission):
    """Requires ``X-Requested-With: XMLHttpRequest``.

    A defence-in-depth CSRF check for the cookie-based auth endpoints (login, refresh,
    logout): a plain HTML form or a cross-site ``<img>``/navigation cannot add this header,
    and a cross-origin ``fetch``/XHR that adds it forces a CORS preflight, which only
    succeeds for an allowed origin. This is on top of, not instead of, the refresh cookie's
    own ``SameSite=Strict``.
    """

    message = "This endpoint requires the X-Requested-With: XMLHttpRequest header."

    def has_permission(self, request, view):
        return request.headers.get("X-Requested-With") == "XMLHttpRequest"
