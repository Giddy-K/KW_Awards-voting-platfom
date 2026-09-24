"""Staff accounts and authentication (AUDIT F-05, F-11, F-21, F-22)."""

import pytest
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


def login(api, email, password=PASSWORD):
    return api.post(TOKEN_URL, {"email": email, "password": password})


def test_f05_no_public_user_endpoints(api):
    """The old /users/ viewset exposed password hashes and let anyone escalate privileges."""
    user = make_user()
    for path in ("/api/v1/users/", f"/api/v1/users/{user.pk}/", "/api/v1/auth/signup/", "/api/users/"):
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


def test_login_returns_working_token_pair(api):
    user = make_user(MODERATOR)
    response = login(api, user.email)
    assert response.status_code == 200
    assert set(response.data) == {"access", "refresh"}
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
    assert api.get(ME_URL).data["email"] == user.email


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


def test_staff_logins_are_audited(api):
    user = make_user()
    login(api, user.email)
    login(api, user.email, "wrong-password")
    ok = AuditLog.objects.get(action=AuditAction.STAFF_LOGIN)
    assert ok.actor_user == user
    failed = AuditLog.objects.get(action=AuditAction.STAFF_LOGIN_FAILED)
    assert failed.metadata["email"] == user.email
    assert "password" not in str(failed.metadata).lower()


def test_refresh_rotates_and_blacklists_the_old_token(api):
    user = make_user()
    pair = login(api, user.email).data
    first = api.post(REFRESH_URL, {"refresh": pair["refresh"]})
    assert first.status_code == 200
    assert first.data["refresh"] != pair["refresh"]
    # Re-using the rotated (now blacklisted) token must fail.
    assert api.post(REFRESH_URL, {"refresh": pair["refresh"]}).status_code == 401


def test_logout_blacklists_refresh_token(api):
    user = make_user()
    pair = login(api, user.email).data
    client = staff_client(user)
    assert client.post(LOGOUT_URL, {"refresh": pair["refresh"]}).status_code == 204
    assert api.post(REFRESH_URL, {"refresh": pair["refresh"]}).status_code == 401


def test_logout_cannot_revoke_someone_elses_token(api):
    victim, attacker = make_user(), make_user()
    pair = login(api, victim.email).data
    response = staff_client(attacker).post(LOGOUT_URL, {"refresh": pair["refresh"]})
    assert response.status_code == 400
    # The victim's token still works.
    assert api.post(REFRESH_URL, {"refresh": pair["refresh"]}).status_code == 200


def test_logout_requires_authentication_and_valid_token(api):
    assert api.post(LOGOUT_URL, {"refresh": "x"}).status_code == 401
    assert staff_client(make_user()).post(LOGOUT_URL, {"refresh": "garbage"}).status_code == 400
    assert staff_client(make_user()).post(LOGOUT_URL, {}).status_code == 400


def test_voter_token_is_not_accepted_as_staff_auth(api):
    voter = make_voter()
    client = voter_client(voter)
    assert client.get(ME_URL).status_code == 401
    assert client.post(LOGOUT_URL, {"refresh": "x"}).status_code == 401


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
