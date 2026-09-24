"""Unit tests for the shared building blocks: phone, text, IP, CAPTCHA, SMS, log masking."""

import logging

import pytest
import requests
from django.test import RequestFactory

from common.captcha import (
    DummyCaptchaBackend,
    TurnstileCaptchaBackend,
    get_captcha_backend,
    verify_captcha,
)
from common.ip import get_client_ip
from common.log_filters import PhoneMaskFilter, mask_phones_in
from common.phone import InvalidPhoneNumber, mask_phone, normalize_phone
from common.sms import (
    AfricasTalkingSmsBackend,
    ConsoleSmsBackend,
    LocMemSmsBackend,
    SmsBackend,
    SmsError,
    get_sms_backend,
    send_sms,
)
from common.text import clean_text
from common.throttles import parse_rate

rf = RequestFactory()


class ExplodingSms(SmsBackend):
    def send(self, to_e164, body):
        raise SmsError("provider down")


# --- phone ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    ["0712345678", "+254712345678", "254712345678", "0712 345 678", " 0712-345-678 ", "0112345678"],
)
def test_kenyan_mobile_numbers_normalise_to_e164(raw):
    assert normalize_phone(raw).startswith("+254")
    assert normalize_phone("0712345678") == "+254712345678"


@pytest.mark.parametrize(
    "raw", ["", "  ", None, 12345, "abc", "123", "+1 555 0100", "020 1234567", "0" * 40]
)
def test_invalid_or_non_mobile_numbers_are_rejected(raw):
    with pytest.raises(InvalidPhoneNumber):
        normalize_phone(raw)


def test_international_numbers_work_with_a_country_code():
    assert normalize_phone("+255712345678") == "+255712345678"  # Tanzania mobile


@pytest.mark.parametrize(
    "value,masked",
    [
        ("+254712345612", "+2547******12"),
        ("+255712345678", "+2557******78"),
        ("", ""),
        (None, ""),
        ("+25471", "******"),
    ],
)
def test_mask_phone(value, masked):
    assert mask_phone(value) == masked


# --- text -------------------------------------------------------------------------


def test_clean_text_strips_markup_control_characters_and_collapses_whitespace():
    assert clean_text("  Hello   <b>world</b>\x00\x07 ") == "Hello world"
    assert clean_text("<script>alert(1)</script>x") == "alert(1)x"
    assert clean_text("a\nb\r\nc") == "a b c"
    assert clean_text(None) == ""
    assert clean_text("Café", multiline=False) == "Café"


def test_clean_text_multiline_keeps_paragraphs_but_limits_blank_lines():
    assert (
        clean_text("one\n\n\n\n\ntwo  words\n three ", multiline=True) == "one\n\ntwo words\nthree"
    )


# --- client IP ---------------------------------------------------------------------


def request_from(remote, forwarded=None):
    extra = {"REMOTE_ADDR": remote}
    if forwarded is not None:
        extra["HTTP_X_FORWARDED_FOR"] = forwarded
    return rf.get("/", **extra)


def test_forwarded_header_is_ignored_when_no_proxy_is_trusted(settings):
    settings.TRUSTED_PROXIES = []
    assert get_client_ip(request_from("198.51.100.9", "6.6.6.6")) == "198.51.100.9"


def test_forwarded_header_is_ignored_when_the_peer_is_not_a_trusted_proxy(settings):
    settings.TRUSTED_PROXIES = ["10.0.0.0/8"]
    assert get_client_ip(request_from("198.51.100.9", "6.6.6.6")) == "198.51.100.9"


def test_forwarded_header_is_used_when_the_peer_is_a_trusted_proxy(settings):
    settings.TRUSTED_PROXIES = ["10.0.0.0/8"]
    assert get_client_ip(request_from("10.0.0.5", "203.0.113.7")) == "203.0.113.7"
    assert get_client_ip(request_from("10.0.0.5", "2001:db8::1")) == "2001:db8::1"


def test_a_chain_through_several_trusted_proxies_resolves_to_the_client(settings):
    """CDN (10.x) in front of nginx (172.16.x): list them outermost first."""
    settings.TRUSTED_PROXIES = ["10.0.0.0/8", "172.16.0.0/12"]
    assert get_client_ip(request_from("172.16.0.9", "203.0.113.7, 10.0.0.2")) == "203.0.113.7"


def test_the_peer_must_be_the_last_trusted_hop(settings):
    settings.TRUSTED_PROXIES = ["10.0.0.0/8", "172.16.0.0/12"]
    assert get_client_ip(request_from("10.0.0.5", "6.6.6.6")) == "10.0.0.5"


def test_spoofed_prefixes_fail_safe_to_the_proxy_address(settings):
    settings.TRUSTED_PROXIES = ["10.0.0.0/8"]
    assert get_client_ip(request_from("10.0.0.5", "6.6.6.6, 203.0.113.7")) == "10.0.0.5"


def test_garbage_or_missing_forwarded_headers_fall_back_to_the_peer(settings):
    settings.TRUSTED_PROXIES = ["10.0.0.0/8"]
    assert get_client_ip(request_from("10.0.0.5", "not-an-ip")) == "10.0.0.5"
    assert get_client_ip(request_from("10.0.0.5", "")) == "10.0.0.5"
    assert get_client_ip(request_from("10.0.0.5")) == "10.0.0.5"
    assert get_client_ip(request_from("not-an-ip", "6.6.6.6")) == "not-an-ip"


def test_malformed_trusted_proxy_settings_fail_the_system_check(settings):
    from common.apps import check_trusted_proxies

    settings.TRUSTED_PROXIES = ["10.0.0.0/8", "nonsense", "10.0."]
    errors = check_trusted_proxies(None)
    assert [e.id for e in errors] == ["common.E001", "common.E001"]
    settings.TRUSTED_PROXIES = ["10.0.0.5", "2001:db8::/32"]
    assert check_trusted_proxies(None) == []


# --- CAPTCHA -----------------------------------------------------------------------


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload, self.status = payload, status

    def raise_for_status(self):
        if self.status >= 400:
            raise requests.HTTPError(str(self.status))

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def test_dummy_captcha_always_passes():
    assert DummyCaptchaBackend().verify("", None) is True


def test_turnstile_success_sends_secret_token_and_ip(monkeypatch):
    seen = {}

    def fake_post(url, data, timeout):
        seen.update(url=url, data=data, timeout=timeout)
        return FakeResponse({"success": True})

    monkeypatch.setattr("common.captcha.requests.post", fake_post)
    assert TurnstileCaptchaBackend(secret="s3cret").verify("tok", "203.0.113.7") is True
    assert seen["url"].startswith("https://challenges.cloudflare.com/")
    assert seen["data"] == {"secret": "s3cret", "response": "tok", "remoteip": "203.0.113.7"}


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse({"success": False}),
        FakeResponse({}),
        FakeResponse({}, 500),
        FakeResponse(ValueError("bad json")),
    ],
)
def test_turnstile_fails_closed_on_rejection_or_errors(monkeypatch, response):
    monkeypatch.setattr("common.captcha.requests.post", lambda *a, **k: response)
    assert TurnstileCaptchaBackend(secret="s").verify("tok") is False


def test_turnstile_fails_closed_on_network_errors_and_missing_input(monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("down")

    monkeypatch.setattr("common.captcha.requests.post", boom)
    assert TurnstileCaptchaBackend(secret="s").verify("tok") is False
    assert TurnstileCaptchaBackend(secret="s").verify("") is False
    assert TurnstileCaptchaBackend(secret="").verify("tok") is False


def test_captcha_backend_selection(settings):
    settings.CAPTCHA_BACKEND, settings.TURNSTILE_SECRET_KEY = "auto", ""
    assert isinstance(get_captcha_backend(), DummyCaptchaBackend)
    settings.TURNSTILE_SECRET_KEY = "key"
    assert isinstance(get_captcha_backend(), TurnstileCaptchaBackend)
    settings.CAPTCHA_BACKEND = "dummy"
    assert isinstance(get_captcha_backend(), DummyCaptchaBackend)
    settings.CAPTCHA_BACKEND = "turnstile"
    assert isinstance(get_captcha_backend(), TurnstileCaptchaBackend)
    settings.CAPTCHA_BACKEND = "tests.media_helpers.RejectingCaptcha"
    assert verify_captcha("x") is False


# --- SMS ---------------------------------------------------------------------------


def test_console_sms_masks_the_number_in_logs(capsys, caplog):
    with caplog.at_level(logging.INFO, logger="common.sms"):
        ConsoleSmsBackend().send("+254712345612", "code 123456")
    out = capsys.readouterr().out
    assert "+2547******12" in out and "+254712345612" not in out and "123456" in out
    assert "+254712345612" not in caplog.text


def test_locmem_sms_collects_messages():
    LocMemSmsBackend.outbox.clear()
    send_sms("+254712345612", "hello")
    assert LocMemSmsBackend.outbox == [{"to": "+254712345612", "body": "hello"}]


def test_sms_backend_selection(settings):
    settings.SMS_BACKEND, settings.AFRICASTALKING_USERNAME, settings.AFRICASTALKING_API_KEY = (
        "auto",
        "",
        "",
    )
    assert isinstance(get_sms_backend(), ConsoleSmsBackend)
    settings.AFRICASTALKING_USERNAME, settings.AFRICASTALKING_API_KEY = "user", "key"
    assert isinstance(get_sms_backend(), AfricasTalkingSmsBackend)
    for name, cls in (
        ("console", ConsoleSmsBackend),
        ("locmem", LocMemSmsBackend),
        ("africastalking", AfricasTalkingSmsBackend),
    ):
        settings.SMS_BACKEND = name
        assert isinstance(get_sms_backend(), cls)
    settings.SMS_BACKEND = "tests.test_common.ExplodingSms"
    assert isinstance(get_sms_backend(), ExplodingSms)


def test_africas_talking_posts_credentials_and_accepts_success(monkeypatch):
    seen = {}

    def fake_post(url, data, headers, timeout):
        seen.update(url=url, data=data, headers=headers)
        return FakeResponse({"SMSMessageData": {"Recipients": [{"statusCode": 101}]}})

    monkeypatch.setattr("common.sms.requests.post", fake_post)
    AfricasTalkingSmsBackend("kwawards", "api-key", "KWAWARDS", sandbox=False).send(
        "+254712345678", "hi"
    )
    assert seen["url"] == "https://api.africastalking.com/version1/messaging"
    assert seen["headers"]["apiKey"] == "api-key"
    assert seen["data"] == {
        "username": "kwawards",
        "to": "+254712345678",
        "message": "hi",
        "from": "KWAWARDS",
    }
    AfricasTalkingSmsBackend("sandbox", "k", "", sandbox=True).send("+254712345678", "hi")
    assert "sandbox" in seen["url"] and "from" not in seen["data"]


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse({"SMSMessageData": {"Recipients": []}}),
        FakeResponse({"SMSMessageData": {"Recipients": [{"statusCode": 403}]}}),
        FakeResponse({}, 500),
        FakeResponse(ValueError("bad json")),
    ],
)
def test_africas_talking_errors_raise_sms_error_without_leaking_the_number(monkeypatch, response):
    monkeypatch.setattr("common.sms.requests.post", lambda *a, **k: response)
    with pytest.raises(SmsError) as info:
        AfricasTalkingSmsBackend("u", "k", "", sandbox=True).send("+254712345678", "hi")
    assert "+254712345678" not in str(info.value)


def test_africas_talking_network_errors_raise_sms_error(monkeypatch):
    def boom(*a, **k):
        raise requests.Timeout("slow")

    monkeypatch.setattr("common.sms.requests.post", boom)
    with pytest.raises(SmsError):
        AfricasTalkingSmsBackend("u", "k", "", sandbox=True).send("+254712345678", "hi")


# --- logging and throttle parsing -----------------------------------------------------


def test_phone_numbers_are_masked_in_log_records():
    record = logging.LogRecord(
        "x",
        logging.INFO,
        __file__,
        1,
        "sent to %s and %s (%d)",
        ("+254712345612", "0712345678", 5),
        None,
    )
    assert PhoneMaskFilter().filter(record)
    rendered = record.getMessage()
    assert "+254712345612" not in rendered and "0712345678" not in rendered
    assert "+2547******12" in rendered and "(5)" in rendered
    assert mask_phones_in("call +254712345612 now") == "call +2547******12 now"
    assert mask_phones_in("order 1234567 of 20260924") == "order 1234567 of 20260924"  # too short


def test_dict_style_log_args_are_masked():
    record = logging.LogRecord(
        "x", logging.INFO, __file__, 1, "%(p)s", ({"p": "+254712345612"},), None
    )
    PhoneMaskFilter().filter(record)
    assert "+254712345612" not in record.getMessage()


@pytest.mark.parametrize(
    "rate,expected",
    [
        ("3/15m", (3, 900)),
        ("10/1d", (10, 86400)),
        ("5/h", (5, 3600)),
        ("1/30s", (1, 30)),
        (" 2 / 2m ", (2, 120)),
    ],
)
def test_parse_rate(rate, expected):
    assert parse_rate(rate) == expected


@pytest.mark.parametrize("bad", ["", "abc", "3/x", "/5m", "3/15", None])
def test_parse_rate_rejects_garbage(bad):
    with pytest.raises(ValueError):
        parse_rate(bad)
