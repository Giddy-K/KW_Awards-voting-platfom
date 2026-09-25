"""OpenAPI documentation for error responses (Phase 2.3). See ``common.exceptions``."""

from rest_framework import serializers

from .exceptions import all_error_codes


class ErrorResponse(serializers.Serializer):
    """The shape of every non-2xx response body (``common.exceptions.exception_handler``).

    ``code``'s choices come from :func:`common.exceptions.all_error_codes`, computed lazily in
    :meth:`get_fields` -- not as a class-level field -- so it reflects every ``ApiError``
    subclass defined anywhere in the app by the time this is actually used (schema generation
    or a real request), regardless of which module happens to import this one first.
    """

    def get_fields(self):
        return {
            "code": serializers.ChoiceField(choices=all_error_codes()),
            "detail": serializers.CharField(),
            "fields": serializers.DictField(
                child=serializers.ListField(child=serializers.CharField()),
                required=False,
                allow_null=True,
                help_text="Per-field validation errors. Present only for validation failures.",
            ),
        }


def error_responses(*statuses):
    """``{400: ErrorResponse, 429: ErrorResponse, ...}`` for ``@extend_schema(responses={...})``."""
    return dict.fromkeys(statuses, ErrorResponse)
