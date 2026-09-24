"""OpenAPI security schemes for the voter token authenticators."""

from drf_spectacular.extensions import OpenApiAuthenticationExtension

_VOTER_SCHEME = {
    "type": "http",
    "scheme": "bearer",
    "bearerFormat": "JWT",
    "description": (
        "Short-lived voter token from `POST /api/v1/voters/otp/verify/`. "
        "Send as `Authorization: Bearer <token>`. Staff tokens are not accepted."
    ),
}


class VoterTokenScheme(OpenApiAuthenticationExtension):
    target_class = "voting.authentication.VoterTokenAuthentication"
    name = "voterAuth"

    def get_security_definition(self, auto_schema):
        return _VOTER_SCHEME


class VoterOrStaffScheme(OpenApiAuthenticationExtension):
    """Either a voter token or a staff JWT (each endpoint's permissions decide which)."""

    target_class = "voting.authentication.VoterOrStaffAuthentication"
    name = "voterAuth"

    def get_security_definition(self, auto_schema):
        return _VOTER_SCHEME
