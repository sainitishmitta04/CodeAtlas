"""Source errors carry a user-safe message and the HTTP status the API should return."""


class SourceError(Exception):
    status = 400
    code = "invalid_source"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class InvalidSource(SourceError):
    pass


class SourceTooLarge(SourceError):
    status = 413
    code = "too_large"


class SourceNotFound(SourceError):
    status = 404
    code = "not_found"


class RateLimited(SourceError):
    status = 429
    code = "rate_limited"


class DownloadFailed(SourceError):
    status = 502
    code = "download_failed"


class TargetConflict(SourceError):
    """The computed project id is already used by a different source (never overwritten)."""
    status = 409
    code = "target_conflict"
