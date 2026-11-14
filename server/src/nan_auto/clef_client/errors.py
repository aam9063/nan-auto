"""Clef failure types.

Kept together so a caller can fail open on `ClefError` without importing the
parser or the transport module, and so a malformed body is never mistaken for
an unhandled crash (spec 5.3: fail open, never block the request).
"""


class ClefError(RuntimeError):
    """Base class for every Clef failure the router can fall open on."""


class ClefUnavailable(ClefError):
    """Clef did not answer usefully: timeout, 429 sustained, 5xx or a broken body."""


class ClefAuthError(ClefError):
    """The key was rejected; retrying cannot help."""


class ClefFormatError(ClefError):
    """The payload does not match the contract the router depends on."""
