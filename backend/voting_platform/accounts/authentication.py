from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication


class StaffJWTAuthentication(JWTAuthentication):
    """Authenticates staff with a JWT *access* token (``AUTH_TOKEN_CLASSES``).

    Voter tokens carry ``token_type="voter"`` and are rejected here as invalid.
    """

    def get_user(self, validated_token):
        user = super().get_user(validated_token)  # also enforces is_active
        if not user.is_staff:
            raise AuthenticationFailed("This account is not allowed to use the staff API.")
        return user
