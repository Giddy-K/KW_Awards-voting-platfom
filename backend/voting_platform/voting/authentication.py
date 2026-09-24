"""Voter tokens: a separate, short-lived JWT type for phone-verified voters.

A voter token is signed like a staff JWT but carries ``token_type="voter"`` and
``scope="vote"``. Staff authentication only accepts ``token_type="access"``, and voter
authentication only accepts ``token_type="voter"``, so neither can be used in place of the
other.
"""

from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import BasePermission
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import Token

from accounts.authentication import StaffJWTAuthentication

from .models import Voter

VOTER_SCOPE = "vote"


class VoterToken(Token):
    token_type = "voter"

    @property
    def lifetime(self):
        return settings.VOTER_TOKEN_LIFETIME


@dataclass
class VoterPrincipal:
    """What ``request.user`` is for a voter-authenticated request."""

    voter: Voter
    is_authenticated: bool = True
    is_anonymous: bool = False

    @property
    def pk(self):
        return self.voter.pk

    @property
    def is_active(self):
        return not self.voter.is_blocked


def issue_voter_token(voter):
    """Return ``(token_string, lifetime_seconds)`` for a verified voter."""
    token = VoterToken()
    token["voter_id"] = str(voter.pk)
    token["scope"] = VOTER_SCOPE
    return str(token), int(settings.VOTER_TOKEN_LIFETIME.total_seconds())


class VoterTokenAuthentication(BaseAuthentication):
    def authenticate(self, request):
        parts = get_authorization_header(request).split()
        if not parts or parts[0].lower() != b"bearer":
            return None
        if len(parts) != 2:
            raise AuthenticationFailed("Invalid Authorization header.")
        try:
            token = VoterToken(parts[1].decode())  # checks signature, expiry and token type
        except (TokenError, UnicodeError) as exc:
            raise AuthenticationFailed("Invalid or expired voter token.") from exc
        if token.get("scope") != VOTER_SCOPE:
            raise AuthenticationFailed("Token scope does not allow voting.")
        try:
            voter = Voter.objects.filter(pk=token.get("voter_id")).first()
        except (DjangoValidationError, ValueError, TypeError) as exc:
            raise AuthenticationFailed("Invalid voter token.") from exc
        if voter is None or voter.is_blocked or voter.verified_at is None:
            raise AuthenticationFailed("Voter is not allowed to vote.")
        return VoterPrincipal(voter), token

    def authenticate_header(self, request):
        return 'Bearer realm="voter"'


class VoterOrStaffAuthentication(BaseAuthentication):
    """Used only where one URL serves both audiences (``/votes/``): voters cast, staff review.

    Each token type is verified by its own authenticator; authorization is then decided by
    the view's permission classes (``IsVoter`` vs the role permissions).
    """

    def authenticate(self, request):
        if not get_authorization_header(request):
            return None
        try:
            return VoterTokenAuthentication().authenticate(request)
        except AuthenticationFailed:
            pass
        return StaffJWTAuthentication().authenticate(request)

    def authenticate_header(self, request):
        return "Bearer"


class IsVoter(BasePermission):
    message = "A verified voter token is required."

    def has_permission(self, request, view):
        return isinstance(request.user, VoterPrincipal)
