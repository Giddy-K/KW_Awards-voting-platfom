"""The staff refresh-token cookie.

The refresh token is never returned in a JSON response body; it only ever travels as an
HttpOnly cookie, scoped to the auth endpoints, so JavaScript on the page can't read it (an
XSS payload can still make same-site requests with it attached, but can't exfiltrate the
value itself). See ``REFRESH_COOKIE_*`` in ``voting_platform.settings.base``.
"""

from django.conf import settings


def set_refresh_cookie(response, refresh_token):
    max_age = int(settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"].total_seconds())
    response.set_cookie(
        settings.REFRESH_COOKIE_NAME,
        str(refresh_token),
        max_age=max_age,
        path=settings.REFRESH_COOKIE_PATH,
        secure=settings.REFRESH_COOKIE_SECURE,
        httponly=True,
        samesite=settings.REFRESH_COOKIE_SAMESITE,
    )


def clear_refresh_cookie(response):
    response.delete_cookie(
        settings.REFRESH_COOKIE_NAME,
        path=settings.REFRESH_COOKIE_PATH,
        samesite=settings.REFRESH_COOKIE_SAMESITE,
    )
