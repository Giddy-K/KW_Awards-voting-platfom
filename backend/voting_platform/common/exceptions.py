"""API errors with a stable machine-readable ``code`` next to the human message."""

from rest_framework import status
from rest_framework.exceptions import APIException, ErrorDetail


class ApiError(APIException):
    """``{"detail": "...", "code": "..."}`` with the given HTTP status."""

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
