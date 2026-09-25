"""API errors with a stable machine-readable ``code`` next to the human message.

Phase 2.3: every non-2xx response, whatever raised it, goes through :func:`exception_handler`
(``REST_FRAMEWORK["EXCEPTION_HANDLER"]``) and comes out as the same envelope --
``{"code": ..., "detail": ..., "fields": ...}`` -- documented by :class:`ErrorResponse`. See
``tests/test_error_responses.py``.
"""

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404
from rest_framework import status
from rest_framework.exceptions import APIException, ErrorDetail
from rest_framework.exceptions import AuthenticationFailed as DRFAuthenticationFailed
from rest_framework.exceptions import MethodNotAllowed as DRFMethodNotAllowed
from rest_framework.exceptions import NotAuthenticated as DRFNotAuthenticated
from rest_framework.exceptions import NotFound as DRFNotFound
from rest_framework.exceptions import ParseError as DRFParseError
from rest_framework.exceptions import PermissionDenied as DRFPermissionDenied
from rest_framework.exceptions import Throttled as DRFThrottled
from rest_framework.exceptions import UnsupportedMediaType as DRFUnsupportedMediaType
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.views import exception_handler as drf_exception_handler
from rest_framework_simplejwt.exceptions import InvalidToken


class ApiError(APIException):
    """``{"detail": "...", "code": "..."}`` with the given HTTP status.

    Every subclass's ``default_code`` is collected into :class:`ErrorResponse`'s ``code`` enum
    automatically (:func:`all_error_codes`) -- adding a new error type here (or anywhere else
    in the app; the collection is recursive and codebase-wide) is enough to document it, no
    separate registration step. ``tests/test_error_responses.py`` independently scans the
    source for every ``code=``/``default_code=`` literal actually used and fails if the enum
    (this collection) doesn't cover one, so the two can't silently drift apart.
    """

    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "Request could not be processed."
    default_code = "error"

    def __init__(self, detail=None, code=None, status_code=None):
        super().__init__(detail=detail, code=code)
        if status_code is not None:
            self.status_code = status_code
        code = code or self.default_code
        self.detail = {
            "detail": ErrorDetail(str(detail or self.default_detail), code),
            "code": code,
        }


class VotingClosed(ApiError):
    status_code = status.HTTP_403_FORBIDDEN
    default_detail = "Voting is not open for this event."
    default_code = "voting_closed"


class NominationsClosed(ApiError):
    status_code = status.HTTP_403_FORBIDDEN
    default_detail = "Nominations are not open for this event."
    default_code = "nominations_closed"


class AlreadyVoted(ApiError):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "You have already voted in this award."
    default_code = "already_voted"


class Conflict(ApiError):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "The request conflicts with the current state."
    default_code = "conflict"


class AlreadyVoided(Conflict):
    default_detail = "This vote has already been voided."
    default_code = "already_voided"


class AlreadyReviewed(Conflict):
    default_detail = "This nomination has already been reviewed."
    default_code = "already_reviewed"


class DuplicateNomination(Conflict):
    default_detail = "This nominee has already been nominated for this award."
    default_code = "duplicate_nomination"


class InvalidTransition(ApiError):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "That status change is not allowed from the current status."
    default_code = "invalid_transition"


class WindowRequired(ApiError):
    default_detail = "Set the required date window first."
    default_code = "window_required"


class CaptchaFailed(ApiError):
    default_detail = "CAPTCHA verification failed."
    default_code = "captcha_failed"


class UnsupportedPhoneNumber(ApiError):
    """The one intentional exception to the OTP flow's phone-existence-blind responses
    (Phase 2.2): whether a number is a valid Kenyan mobile is public information, unlike
    whether that specific number is a registered voter. See ``OTP_ALLOWED_REGIONS``.
    """

    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = "Enter a valid Kenyan mobile phone number."
    default_code = "unsupported_phone_number"


# Built-in DRF/SimpleJWT exceptions this API's endpoints can actually raise, whose
# `default_code` is pulled in the same way as our own ApiError subclasses (never hand-typed as
# a string) -- see `all_error_codes`.
_THIRD_PARTY_CODE_SOURCES = [
    DRFAuthenticationFailed,
    DRFNotAuthenticated,
    DRFPermissionDenied,
    DRFNotFound,
    DRFThrottled,
    DRFValidationError,
    DRFMethodNotAllowed,
    DRFParseError,
    DRFUnsupportedMediaType,
    InvalidToken,
]

# SimpleJWT's TokenObtainSerializer/TokenRefreshSerializer raise DRF's own AuthenticationFailed
# with this code as a bare string literal (twice, in rest_framework_simplejwt.serializers),
# not as a dedicated exception class -- there is no importable constant to pull it from. It
# reaches our API via StaffTokenObtainPairSerializer.validate (wrong credentials) and, by us
# explicitly reusing the same string, the "authenticated but not staff" case too.
_EXTRA_THIRD_PARTY_CODES = {"no_active_account"}


def _api_error_subclasses(cls=ApiError):
    for sub in cls.__subclasses__():
        yield sub
        yield from _api_error_subclasses(sub)


def all_error_codes():
    """Every ``code`` this API can return: every :class:`ApiError` subclass's ``default_code``
    (found by walking ``__subclasses__()``, so this only sees classes that have actually been
    imported -- callers must run after the URL conf is loaded, which is always true by the time
    a view is dispatched or a schema is generated) plus the built-in DRF/SimpleJWT codes above.
    """
    codes = {sub.default_code for sub in _api_error_subclasses()}
    codes.update(cls.default_code for cls in _THIRD_PARTY_CODE_SOURCES)
    codes.update(_EXTRA_THIRD_PARTY_CODES)
    return sorted(codes)


def exception_handler(exc, context):
    """``REST_FRAMEWORK["EXCEPTION_HANDLER"]``: normalise every non-2xx body to
    ``{"code": ..., "detail": ..., "fields": ...}`` (:class:`ErrorResponse`), whether it came
    from one of our :class:`ApiError` subclasses, a plain serializer ``ValidationError``, or a
    built-in DRF/SimpleJWT exception (401/403/404/405/429/...).
    """
    # DRF's own default handler converts Http404 / Django's PermissionDenied into its own
    # NotFound / PermissionDenied, but only as a local variable inside that function -- it
    # never reassigns our `exc`, so code extraction below would otherwise see the original
    # Django exception (which has no .get_codes()). Do the same conversion here first.
    if isinstance(exc, Http404):
        exc = DRFNotFound(*exc.args)
    elif isinstance(exc, DjangoPermissionDenied):
        exc = DRFPermissionDenied(*exc.args)
    response = drf_exception_handler(exc, context)
    if response is None:
        return None
    retry_after = getattr(exc, "retry_after", None)
    if retry_after is not None:
        response["Retry-After"] = str(retry_after)
    data = response.data
    if isinstance(data, dict) and set(data) == {"detail", "code"}:
        # Our own ApiError shape already; just add the always-present "fields" key.
        response.data = {"code": data["code"], "detail": str(data["detail"]), "fields": None}
        return response
    if isinstance(exc, DRFValidationError):
        # `data` is the usual per-field {"field": ["msg", ...], ...} (or a flat list for a
        # non-field error); carry it under "fields" instead of as the whole body. The per-field
        # codes inside it (exc.get_codes()) are too granular for one top-level `code`, so this
        # uses the class's own default_code ("invalid") instead, same as everywhere else.
        response.data = {
            "code": DRFValidationError.default_code,
            "detail": "Validation failed.",
            "fields": data,
        }
    else:
        # exc.default_code is only the CLASS's fallback; some exceptions (e.g. SimpleJWT's
        # AuthenticationFailed(msg, "no_active_account")) override the code per instance, which
        # only shows up in exc.get_codes() (built from exc.detail), not the class attribute.
        codes = exc.get_codes()
        code = codes if isinstance(codes, str) else getattr(exc, "default_code", "error")
        detail = data.get("detail") if isinstance(data, dict) else data
        response.data = {"code": code, "detail": str(detail), "fields": None}
    return response
