"""Input validation helpers shared by routes and templates."""

import re
from urllib.parse import urlsplit

MAX_MEETING_LINK_LENGTH = 2048

# Dotted hostname such as uta.zoom.us, meet.google.com or 129.107.1.1.
_HOSTNAME_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)
_FORBIDDEN_CHARS_RE = re.compile(r"[\s\\\x00-\x1f\x7f<>\"'`]")


def validate_meeting_link(value):
    """
    Check a user-supplied meeting link.

    Returns (clean_url, error). A blank value is allowed here and comes back as
    ("", None); routes that require a link for Online/Hybrid groups already
    check for blank separately. Only http:// and https:// links with a real
    hostname are accepted. Anything else (javascript:, data:, ftp:, mailto:,
    relative paths, credentials in the URL, malformed input) returns an error.
    """
    text = "" if value is None else str(value).strip()
    if not text:
        return "", None

    error = "Please enter a valid meeting link that starts with https:// or http://."

    if len(text) > MAX_MEETING_LINK_LENGTH or _FORBIDDEN_CHARS_RE.search(text):
        return None, error

    try:
        parts = urlsplit(text)
        port = parts.port  # raises ValueError for an invalid port
    except ValueError:
        return None, error

    if parts.scheme not in ("http", "https"):
        return None, error
    if parts.username is not None or parts.password is not None:
        return None, error

    host = (parts.hostname or "").lower()
    if not _HOSTNAME_RE.match(host) or len(host) > 253:
        return None, error
    if port is not None and not 0 < port < 65536:
        return None, error

    return text, None


def safe_meeting_url(value):
    """
    Template filter: the link if it is safe to use as an href, else None.
    Protects against bad values that are already in the database.
    """
    clean, error = validate_meeting_link(value)
    if error or not clean:
        return None
    return clean
