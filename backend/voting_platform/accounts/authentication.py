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


class PublicOrStaffAuthentication(StaffJWTAuthentication):
    """Default authenticator: staff JWT, but a valid *voter* token is treated as anonymous.

    A browser that keeps a voter token around must still be able to read the public
    endpoints. Voter tokens grant nothing on staff endpoints either way (anonymous is denied
    there). Any other bad token (garbage, expired, wrong signature) still fails with 401, so
    staff clients keep seeing ``token_not_valid`` and can refresh.
    """

    def authenticate(self, request):
        header = self.get_header(request)
        if header is not None:
            raw_token = self.get_raw_token(header)
            if raw_token is not None and self._is_valid_voter_token(raw_token):
                return None
        return super().authenticate(request)

    @staticmethod
    def _is_valid_voter_token(raw_token):
        from rest_framework_simplejwt.exceptions import TokenError

        from voting.authentication import VoterToken

        try:
            VoterToken(raw_token)
        except (TokenError, UnicodeError):
            return False
        return True
