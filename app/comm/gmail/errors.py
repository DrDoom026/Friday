"""Gmail-specific error types (PART 13)."""


class GmailError(Exception):
    """Base class for every Gmail adapter failure."""


class GmailConfigurationError(GmailError):
    """OAuth is not configured (missing client id/secret/refresh token)."""


class GmailAPIError(GmailError):
    """A call to Google's OAuth token endpoint or the Gmail API failed."""
