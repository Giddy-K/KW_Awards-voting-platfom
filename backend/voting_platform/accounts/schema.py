"""OpenAPI security scheme for staff JWT authentication."""

from drf_spectacular.extensions import OpenApiAuthenticationExtension


class StaffJWTScheme(OpenApiAuthenticationExtension):
    target_class = "accounts.authentication.PublicOrStaffAuthentication"
    name = "staffAuth"
    match_subclasses = True

    def get_security_definition(self, auto_schema):
        return {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": (
                "Staff access token from `POST /api/v1/auth/token/`. "
                "Send as `Authorization: Bearer <access token>`. Voter tokens are not accepted."
            ),
        }
