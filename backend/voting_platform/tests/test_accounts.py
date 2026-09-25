"""Staff accounts and authentication (AUDIT F-05, F-11, F-21, F-22)."""

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from accounts.roles import EVENT_ADMIN, MODERATOR, ensure_roles, user_has_role
from audit.models import AuditAction, AuditLog

from .helpers import PASSWORD, make_event_admin, make_user, make_voter, staff_client, voter_client

pytestmark = pytest.mark.django_db
User = get_user_model()

TOKEN_URL = "/api/v1/auth/token/"
REFRESH_URL = "/api/v1/auth/refresh/"
LOGOUT_URL = "/api/v1/auth/logout/"
ME_URL = "/api/v1/auth/me/"

# Required by HasAjaxHeader on the cookie endpoints (Phase 2.1 CSRF defence).
AJAX = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


def login(api, email, password=PASSWORD, **extra):
    return api.post(TOKEN_URL, {"email": email, "password": password}, **AJAX, **extra)


def refresh_cookie(response):
    """The Morsel for the refresh cookie on a login/refresh response, or None.

    Note: Django's test client cookie jar *mutates the same Morsel object in place* on a
    later ``client.cookies[name] = ...`` assignment, rather than replacing it. Read
    ``.value`` off the Morsel immediately; don't hold the Morsel itself across further
    requests/assignments on the same client, or it will silently change under you.
    """
    return response.cookies.get(settings.REFRESH_COOKIE_NAME)


def test_f05_no_public_user_endpoints(api):
    """The old /users/ viewset exposed password hashes and let anyone escalate privileges."""
    user = make_user()
    for path in (
        "/api/v1/users/",
        f"/api/v1/users/{user.pk}/",
        "/api/v1/auth/signup/",
        "/api/users/",
    ):
        assert api.get(path).status_code == 404, path
        assert api.post(path, {"email": "x@example.com", "password": "x"}).status_code == 404, path


def test_f05_user_model_has_no_type_or_public_fields():
    names = {f.name for f in User._meta.get_fields()}
    assert "type" not in names
    assert {"email", "full_name", "is_active", "is_staff"} <= names


def test_f05_me_never_returns_password_and_lists_roles(api):
    admin = make_event_admin()
    response = staff_client(admin).get(ME_URL)
    assert response.status_code == 200
    assert set(response.data) == {"id", "email", "full_name", "is_staff", "is_superuser", "roles"}
    assert response.data["roles"] == [EVENT_ADMIN]
    assert "password" not in response.content.decode().lower()


def test_me_reports_superuser_role():
    su = make_user(superuser=True)
    assert staff_client(su).get(ME_URL).data["roles"][0] == "superuser"


def test_login_returns_access_token_only_and_sets_the_refresh_cookie(api):
    """Phase 2.1: the refresh token is never in the response body, only an HttpOnly cookie."""
    user = make_user(MODERATOR)
    response = login(api, user.email)
    assert response.status_code == 200
    assert set(response.data) == {"access"}
    assert "refresh" not in response.content.decode()

    cookie = refresh_cookie(response)
    assert cookie is not None and cookie.value
    assert cookie["httponly"] is True
    assert cookie["samesite"] == "Strict"
    assert cookie["path"] == "/api/v1/auth/"
    assert bool(cookie["secure"]) is settings.REFRESH_COOKIE_SECURE  # False in dev/test

    api.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
    assert api.get(ME_URL).data["email"] == user.email


def test_login_requires_the_ajax_header(api):
    user = make_user()
    response = api.post(TOKEN_URL, {"email": user.email, "password": PASSWORD})
    assert response.status_code == 403
    assert refresh_cookie(response) is None


def test_f11_login_is_case_insensitive_on_email(api):
    user = make_user(email="Mixed.Case@Example.com")
    assert user.email == "mixed.case@example.com"
    assert login(api, "MIXED.CASE@EXAMPLE.COM").status_code == 200


def test_login_rejects_bad_password_inactive_and_non_staff(api):
    user = make_user()
    assert login(api, user.email, "wrong-password").status_code == 401
    inactive = make_user(is_active=False)
    assert login(api, inactive.email).status_code == 401
    non_staff = make_user(is_staff=False)
    assert login(api, non_staff.email).status_code == 401


def test_login_failure_is_identical_for_wrong_password_unknown_email_and_inactive_account(api):
    """AUDIT F-11 / Phase 2.3: no_active_account must not let an attacker distinguish a wrong
    password from an unknown email from a deactivated account -- any difference in status,
    code or message would leak which staff emails exist."""
    user = make_user()
    inactive = make_user(is_active=False)
    responses = [
        login(api, user.email, "wrong-password"),
        login(api, "no-such-person@example.com"),
        login(api, inactive.email),
    ]
    bodies = {(r.status_code, r.data["code"], r.data["detail"]) for r in responses}
    assert len(bodies) == 1, [(r.status_code, r.data) for r in responses]
    status_code, code, _detail = bodies.pop()
    assert status_code == 401 and code == "no_active_account"


def test_staff_logins_are_audited(api):
    user = make_user()
    login(api, user.email)
    login(api, user.email, "wrong-password")
    ok = AuditLog.objects.get(action=AuditAction.STAFF_LOGIN)
    assert ok.actor_user == user
    failed = AuditLog.objects.get(action=AuditAction.STAFF_LOGIN_FAILED)
    assert failed.metadata["email"] == user.email
    assert "password" not in str(failed.metadata).lower()


def test_refresh_reads_the_cookie_rotates_it_and_blacklists_the_old_token(api):
    user = make_user()
    login_response = login(api, user.email)
    old_refresh = refresh_cookie(login_response).value

    # api's cookie jar now carries the refresh cookie automatically, like a browser.
    first = api.post(REFRESH_URL, **AJAX)
    assert first.status_code == 200
    assert set(first.data) == {"access"}
    new_refresh = refresh_cookie(first).value  # read .value now; see refresh_cookie's note
    assert new_refresh != old_refresh

    # Re-using the rotated (now blacklisted) old token, presented as the cookie, must fail.
    api.cookies[settings.REFRESH_COOKIE_NAME] = old_refresh
    assert api.post(REFRESH_URL, **AJAX).status_code == 401

    # The new one still works.
    api.cookies[settings.REFRESH_COOKIE_NAME] = new_refresh
    assert api.post(REFRESH_URL, **AJAX).status_code == 200


def test_refresh_without_a_cookie_or_with_the_ajax_header_missing(api):
    no_cookie = api.post(REFRESH_URL, **AJAX)
    assert no_cookie.status_code == 401 and no_cookie.data["code"] == "token_not_valid"

    login(api, make_user().email)
    no_header = api.post(REFRESH_URL)
    assert no_header.status_code == 403


def test_logout_blacklists_the_refresh_cookie_and_clears_it(api):
    user = make_user()
    login_response = login(api, user.email)
    old_refresh = refresh_cookie(login_response).value
    client = staff_client(user)
    client.cookies[settings.REFRESH_COOKIE_NAME] = old_refresh

    logout_response = client.post(LOGOUT_URL, **AJAX)
    assert logout_response.status_code == 204
    cleared = refresh_cookie(logout_response)
    assert cleared is not None and cleared.value == "" and cleared["max-age"] == 0

    api.cookies[settings.REFRESH_COOKIE_NAME] = old_refresh
    assert api.post(REFRESH_URL, **AJAX).status_code == 401


def test_logout_never_blacklists_a_cookie_belonging_to_someone_else(api):
    """The cookie is HttpOnly, so a client can no longer choose whose token to send; the
    view still checks ownership defensively in case a foreign value ends up in the cookie."""
    victim, attacker = make_user(), make_user()
    victim_refresh = refresh_cookie(login(api, victim.email)).value

    attacker_client = staff_client(attacker)
    attacker_client.cookies[settings.REFRESH_COOKIE_NAME] = victim_refresh
    response = attacker_client.post(LOGOUT_URL, **AJAX)
    assert response.status_code == 204  # logout is idempotent even when nothing was revoked

    # The victim's token is untouched.
    api.cookies[settings.REFRESH_COOKIE_NAME] = victim_refresh
    assert api.post(REFRESH_URL, **AJAX).status_code == 200


def test_logout_is_idempotent_with_no_cookie_and_tolerates_a_garbage_one(api):
    user = make_user()
    client = staff_client(user)
    assert client.post(LOGOUT_URL, **AJAX).status_code == 204  # nothing to revoke
    client.cookies[settings.REFRESH_COOKIE_NAME] = "not-a-real-token"
    assert client.post(LOGOUT_URL, **AJAX).status_code == 204  # tolerated, still clears


def test_logout_requires_authentication_and_the_ajax_header(api):
    assert api.post(LOGOUT_URL, **AJAX).status_code == 401
    assert staff_client(make_user()).post(LOGOUT_URL).status_code == 403


def test_voter_token_is_not_accepted_as_staff_auth(api):
    voter = make_voter()
    client = voter_client(voter)
    assert client.get(ME_URL).status_code == 401
    assert client.post(LOGOUT_URL, **AJAX).status_code == 401


def test_garbage_and_missing_tokens_are_unauthorized(api):
    assert api.get(ME_URL).status_code == 401
    api.credentials(HTTP_AUTHORIZATION="Bearer not-a-jwt")
    assert api.get(ME_URL).status_code == 401


def test_deactivated_user_token_stops_working():
    user = make_user()
    client = staff_client(user)
    assert client.get(ME_URL).status_code == 200
    user.is_active = False
    user.save()
    assert client.get(ME_URL).status_code == 401


def test_email_unique_case_insensitively():
    make_user(email="a@example.com")
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.create(email="A@Example.com", full_name="Dup")


def test_create_user_requires_email():
    with pytest.raises(ValueError):
        User.objects.create_user(email="", password=PASSWORD)


def test_superuser_must_be_staff_and_superuser():
    with pytest.raises(ValueError):
        User.objects.create_superuser(email="x@example.com", password=PASSWORD, is_staff=False)


def test_user_display_helpers():
    user = make_user()
    assert str(user) == user.email
    assert user.get_full_name() == user.full_name
    assert user.get_short_name() == user.full_name.split(" ")[0]


def test_f22_weak_passwords_are_rejected_by_validators():
    """The old signup accepted '1234'. Password validators now enforce a real policy."""
    for weak in ("1234", "password", "12345678901"):
        with pytest.raises(ValidationError):
            validate_password(weak)
    validate_password("a-genuinely-long-Passphrase-42")


def test_roles_are_created_idempotently_with_permissions():
    ensure_roles()
    ensure_roles()
    assert set(Group.objects.values_list("name", flat=True)) >= {MODERATOR, EVENT_ADMIN}
    moderator = Group.objects.get(name=MODERATOR)
    assert moderator.permissions.filter(codename="change_nomination").exists()
    assert not moderator.permissions.filter(codename="void_vote").exists()
    admin = Group.objects.get(name=EVENT_ADMIN)
    assert admin.permissions.filter(codename="void_vote").exists()
    assert admin.permissions.filter(codename="view_auditlog").exists()


def test_user_has_role_matrix():
    moderator, admin, su = make_user(MODERATOR), make_user(EVENT_ADMIN), make_user(superuser=True)
    plain = make_user()
    assert user_has_role(moderator, MODERATOR) and not user_has_role(moderator, EVENT_ADMIN)
    assert user_has_role(admin, EVENT_ADMIN) and not user_has_role(admin, MODERATOR)
    assert user_has_role(su, MODERATOR) and user_has_role(su, EVENT_ADMIN)
    assert not user_has_role(plain, MODERATOR)
    assert not user_has_role(None, MODERATOR)
    inactive = make_user(MODERATOR, is_active=False)
    assert not user_has_role(inactive, MODERATOR)


# --- CORS credentials (Phase 2.1) --------------------------------------------------------


def test_cors_allows_credentials_only_for_an_allowed_origin(api):
    assert settings.CORS_ALLOW_CREDENTIALS is True
    allowed_origin = settings.CORS_ALLOWED_ORIGINS[0]
    ok = api.get(ME_URL, HTTP_ORIGIN=allowed_origin)
    assert ok.headers.get("Access-Control-Allow-Origin") == allowed_origin
    assert ok.headers.get("Access-Control-Allow-Credentials") == "true"

    other = api.get(ME_URL, HTTP_ORIGIN="https://not-an-allowed-origin.example")
    assert "Access-Control-Allow-Origin" not in other.headers
    assert "Access-Control-Allow-Credentials" not in other.headers
