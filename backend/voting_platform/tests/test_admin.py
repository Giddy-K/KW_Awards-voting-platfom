"""Django admin: masking, read-only guarantees, audited actions."""

import re

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from audit.models import AuditAction, AuditLog
from events.models import EventStatus
from nominations.models import NominationStatus
from voting.models import OTPChallenge, Vote

from .helpers import (
    PASSWORD,
    make_award,
    make_category,
    make_event,
    make_event_admin,
    make_moderator,
    make_nomination,
    make_user,
    make_voter,
)
from .vote_helpers import make_payment, make_vote

pytestmark = pytest.mark.django_db
User = get_user_model()

FULL_PHONE = "+254712345612"
MASKED = "+2547******12"


def login(client, user):
    client.force_login(user)
    return client


def messages_of(response):
    return [str(m) for m in response.context["messages"]]


def act(client, url, action, ids, **extra):
    data = {"action": action, "_selected_action": [str(i) for i in ids], **extra}
    return client.post(url, data, follow=True)


# --- access -----------------------------------------------------------------------------


def test_anonymous_and_non_staff_are_sent_to_the_login_page(client):
    for url in (reverse("admin:index"), reverse("admin:voting_vote_changelist")):
        response = client.get(url)
        assert response.status_code == 302 and "/admin/login/" in response.url
    outsider = make_user(is_staff=False)
    login(client, outsider)
    assert client.get(reverse("admin:index")).status_code == 302


def test_admin_login_works_with_email_and_ignores_case(client):
    user = make_user(email="Boss@Example.com")
    response = client.post(
        "/admin/login/", {"username": "BOSS@example.com", "password": PASSWORD, "next": "/admin/"}
    )
    assert response.status_code == 302 and response.url == "/admin/"
    assert client.get("/admin/").status_code == 200
    assert user.email == "boss@example.com"


def test_roles_control_which_models_are_visible(client):
    moderator, event_admin = make_moderator(), make_event_admin()
    login(client, moderator)
    assert client.get(reverse("admin:nominations_nomination_changelist")).status_code == 200
    assert client.get(reverse("admin:voting_vote_changelist")).status_code == 403
    assert client.get(reverse("admin:audit_auditlog_changelist")).status_code == 403
    login(client, event_admin)
    assert client.get(reverse("admin:voting_vote_changelist")).status_code == 200
    assert client.get(reverse("admin:audit_auditlog_changelist")).status_code == 200
    assert client.get(reverse("admin:events_event_changelist")).status_code == 200
    assert client.get(reverse("admin:accounts_user_changelist")).status_code == 403


# --- masking ------------------------------------------------------------------------------


def test_voter_phone_is_masked_in_list_detail_and_search(client):
    voter = make_voter(FULL_PHONE)
    login(client, make_user(superuser=True))
    listing = client.get(reverse("admin:voting_voter_changelist"))
    assert MASKED in listing.content.decode() and FULL_PHONE not in listing.content.decode()
    found = client.get(reverse("admin:voting_voter_changelist"), {"q": FULL_PHONE})
    # The search box echoes what the staff member typed; the results themselves must be masked.
    results = re.sub(r'<input[^>]*name="q"[^>]*>', "", found.content.decode())
    assert MASKED in results and FULL_PHONE not in results
    partial = client.get(reverse("admin:voting_voter_changelist"), {"q": "+2547123"})
    assert MASKED not in partial.content.decode()  # exact match only: no prefix probing
    detail = client.get(reverse("admin:voting_voter_change", args=[voter.pk]))
    assert MASKED in detail.content.decode() and FULL_PHONE not in detail.content.decode()


def test_voter_can_be_blocked_but_not_created_or_deleted(client):
    voter = make_voter()
    login(client, make_user(superuser=True))
    assert client.get(reverse("admin:voting_voter_add")).status_code == 403
    assert (
        client.post(
            reverse("admin:voting_voter_delete", args=[voter.pk]), {"post": "yes"}
        ).status_code
        == 403
    )
    url = reverse("admin:voting_voter_change", args=[voter.pk])
    client.post(url, {"is_blocked": "on"})
    voter.refresh_from_db()
    assert voter.is_blocked is True


def test_votes_payments_challenges_and_audit_never_show_full_phone_numbers(client):
    voter = make_voter(FULL_PHONE)
    nomination = make_nomination()
    make_vote(voter, nomination)
    make_payment(voter, nomination)
    OTPChallenge.objects.create(voter=voter, code_hash="x" * 64, expires_at="2030-01-01T00:00:00Z")
    AuditLog.objects.create(action=AuditAction.OTP_REQUESTED, actor_voter=voter)
    login(client, make_user(superuser=True))
    for name in (
        "admin:voting_vote_changelist",
        "admin:payments_payment_changelist",
        "admin:voting_otpchallenge_changelist",
        "admin:audit_auditlog_changelist",
    ):
        body = client.get(reverse(name)).content.decode()
        assert FULL_PHONE not in body, name
        assert MASKED in body, name
    challenge = OTPChallenge.objects.get()
    detail = client.get(
        reverse("admin:voting_otpchallenge_change", args=[challenge.pk])
    ).content.decode()
    assert "x" * 64 not in detail and "code_hash" not in detail
    payment = make_payment(voter, nomination, provider_receipt="RCPT999")
    detail = client.get(
        reverse("admin:payments_payment_change", args=[payment.pk])
    ).content.decode()
    assert FULL_PHONE not in detail


# --- read-only guarantees ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "model", ["voting_vote", "audit_auditlog", "payments_payment", "voting_otpchallenge"]
)
def test_append_only_models_are_read_only_even_for_superusers(client, model):
    login(client, make_user(superuser=True))
    assert client.get(reverse(f"admin:{model}_add")).status_code == 403
    assert client.get(reverse(f"admin:{model}_changelist")).status_code == 200


def test_vote_and_audit_detail_pages_are_view_only_and_reject_edits_and_deletes(client):
    vote = make_vote(make_voter(), make_nomination())
    entry = AuditLog.objects.create(action=AuditAction.VOTE_CAST)
    login(client, make_user(superuser=True))
    for model, obj in (("voting_vote", vote), ("audit_auditlog", entry)):
        assert client.get(reverse(f"admin:{model}_change", args=[obj.pk])).status_code == 200
        assert (
            client.post(reverse(f"admin:{model}_change", args=[obj.pk]), {"x": "y"}).status_code
            == 403
        )
        assert (
            client.post(
                reverse(f"admin:{model}_delete", args=[obj.pk]), {"post": "yes"}
            ).status_code
            == 403
        )
    assert (
        Vote.objects.filter(pk=vote.pk).exists() and AuditLog.objects.filter(pk=entry.pk).exists()
    )
    changelist = client.get(reverse("admin:voting_vote_changelist")).content.decode()
    assert "delete_selected" not in changelist


# --- voiding through the admin -----------------------------------------------------------------


def test_event_admin_can_void_votes_with_a_reason_and_it_is_audited(client):
    admin_user = make_event_admin()
    vote = make_vote(make_voter(), make_nomination())
    login(client, admin_user)
    url = reverse("admin:voting_vote_changelist")
    response = act(client, url, "void_selected", [vote.pk], reason="Bot farm <b>detected</b>")
    assert "Voided 1 vote(s)." in messages_of(response)
    vote.refresh_from_db()
    assert (
        vote.voided_at and vote.voided_by == admin_user and vote.void_reason == "Bot farm detected"
    )
    entry = AuditLog.objects.get(action=AuditAction.VOTE_VOIDED)
    assert entry.actor_user == admin_user and entry.metadata["reason"] == "Bot farm detected"
    again = act(client, url, "void_selected", [vote.pk], reason="again")
    assert "Voided 0 vote(s)." in messages_of(again)


def test_voiding_without_a_reason_is_refused(client):
    vote = make_vote(make_voter(), make_nomination())
    login(client, make_event_admin())
    response = act(
        client, reverse("admin:voting_vote_changelist"), "void_selected", [vote.pk], reason="  "
    )
    assert "Enter a reason to void votes." in messages_of(response)
    vote.refresh_from_db()
    assert vote.voided_at is None


def test_users_without_the_void_permission_cannot_void(client):
    from django.contrib.auth.models import Permission

    vote = make_vote(make_voter(), make_nomination())
    viewer = make_user()
    viewer.user_permissions.add(Permission.objects.get(codename="view_vote"))
    login(client, viewer)
    url = reverse("admin:voting_vote_changelist")
    assert client.get(url).status_code == 200
    act(client, url, "void_selected", [vote.pk], reason="sneaky")
    vote.refresh_from_db()
    assert vote.voided_at is None


# --- nominations ---------------------------------------------------------------------------------


def test_moderators_approve_and_reject_through_actions_with_audit(client):
    moderator = make_moderator()
    award = make_award(make_category(make_event()))
    to_approve = make_nomination(award, status=NominationStatus.PENDING)
    to_reject = make_nomination(award, status=NominationStatus.PENDING)
    login(client, moderator)
    url = reverse("admin:nominations_nomination_changelist")
    response = act(client, url, "approve_selected", [to_approve.pk])
    assert "Approved 1 nomination(s)." in messages_of(response)
    to_approve.refresh_from_db()
    assert to_approve.status == "approved" and to_approve.reviewed_by == moderator
    refused = act(client, url, "reject_selected", [to_reject.pk], reason="")
    assert "Enter a reason to reject nominations." in messages_of(refused)
    to_reject.refresh_from_db()
    assert to_reject.status == "pending"
    done = act(client, url, "reject_selected", [to_reject.pk], reason="Duplicate")
    assert "Rejected 1 nomination(s)." in messages_of(done)
    to_reject.refresh_from_db()
    assert to_reject.status == "rejected" and to_reject.rejection_reason == "Duplicate"
    kinds = set(AuditLog.objects.filter(actor_user=moderator).values_list("action", flat=True))
    assert {"nomination.approved", "nomination.rejected"} <= kinds


def test_only_pending_nominations_are_touched_by_bulk_actions(client):
    login(client, make_moderator())
    pending = make_nomination(status=NominationStatus.PENDING)
    approved = make_nomination(status=NominationStatus.APPROVED)
    response = act(
        client,
        reverse("admin:nominations_nomination_changelist"),
        "approve_selected",
        [pending.pk, approved.pk],
    )
    assert any("Skipped 1" in m for m in messages_of(response))


def test_nomination_status_cannot_be_edited_directly_or_added_in_admin(client):
    nomination = make_nomination(status=NominationStatus.PENDING)
    login(client, make_moderator())
    assert client.get(reverse("admin:nominations_nomination_add")).status_code == 403
    url = reverse("admin:nominations_nomination_change", args=[nomination.pk])
    assert client.get(url).status_code == 200
    client.post(url, {"status": "approved"})
    nomination.refresh_from_db()
    assert nomination.status == "pending"  # only the audited actions change it


def test_moderators_cannot_delete_nominations_but_superusers_can(client):
    nomination = make_nomination()
    login(client, make_moderator())
    url = reverse("admin:nominations_nomination_delete", args=[nomination.pk])
    assert client.post(url, {"post": "yes"}).status_code == 403
    login(client, make_user(superuser=True))
    assert client.post(url, {"post": "yes"}).status_code == 302


def test_nominee_admin_lists_and_shows_photo_flag(client):
    nomination = make_nomination()
    login(client, make_user(superuser=True))
    assert client.get(reverse("admin:nominations_nominee_changelist")).status_code == 200
    assert (
        client.get(
            reverse("admin:nominations_nominee_change", args=[nomination.nominee.pk])
        ).status_code
        == 200
    )


# --- events ----------------------------------------------------------------------------------------


def test_event_status_changes_go_through_the_audited_service(client):
    event = make_event(status=EventStatus.DRAFT)
    admin_user = make_event_admin()
    login(client, admin_user)
    url = reverse("admin:events_event_changelist")
    ok = act(client, url, "change_status", [event.pk], new_status="nominations_open")
    assert any("is now nominations_open" in m for m in messages_of(ok))
    event.refresh_from_db()
    assert event.status == "nominations_open"
    assert AuditLog.objects.filter(
        action=AuditAction.EVENT_STATUS_CHANGED, actor_user=admin_user
    ).exists()
    bad = act(client, url, "change_status", [event.pk], new_status="results_published")
    assert any("Cannot change status" in m for m in messages_of(bad))
    none = act(client, url, "change_status", [event.pk], new_status="")
    assert "Choose the new status first." in messages_of(none)
    event.refresh_from_db()
    assert event.status == "nominations_open"


def test_event_status_field_is_read_only_in_the_form(client):
    event = make_event(status=EventStatus.DRAFT)
    login(client, make_event_admin())
    url = reverse("admin:events_event_change", args=[event.pk])
    response = client.get(url)
    assert response.status_code == 200 and 'name="status"' not in response.content.decode()


def test_categories_and_awards_can_be_managed_by_event_admins(client):
    award = make_award()
    login(client, make_event_admin())
    for name, obj in (("events_category", award.category), ("events_award", award)):
        assert client.get(reverse(f"admin:{name}_changelist")).status_code == 200
        assert client.get(reverse(f"admin:{name}_change", args=[obj.pk])).status_code == 200


# --- staff users --------------------------------------------------------------------------------------


def test_superusers_can_create_staff_accounts_and_roles_are_listed(client):
    from django.contrib.auth.models import Group

    login(client, make_user(superuser=True))
    add = reverse("admin:accounts_user_add")
    assert client.get(add).status_code == 200
    weak = client.post(
        add,
        {
            "email": "new@example.com",
            "full_name": "New Person",
            "password1": "1234",
            "password2": "1234",
        },
    )
    assert weak.status_code == 200 and not User.objects.filter(email="new@example.com").exists()
    group = Group.objects.get(name="Moderator")
    ok = client.post(
        add,
        {
            "email": "New@Example.com",
            "full_name": "New Person",
            "password1": "A-long-Passphrase-42",
            "password2": "A-long-Passphrase-42",
            "groups": [group.pk],
        },
    )
    assert ok.status_code == 302
    created = User.objects.get(email="new@example.com")
    assert created.is_staff and not created.is_superuser and list(created.groups.all()) == [group]
    listing = client.get(reverse("admin:accounts_user_changelist")).content.decode()
    assert "Moderator" in listing and "new@example.com" in listing
    detail = client.get(reverse("admin:accounts_user_change", args=[created.pk]))
    assert detail.status_code == 200
