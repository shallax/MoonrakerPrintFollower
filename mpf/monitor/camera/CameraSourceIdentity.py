"""Preserve feed selectors while ignoring known rotating transport parameters."""
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

# Keep this list aligned with CameraViewport._stateKey. Unknown parameters
# can select a different physical camera and must remain part of its identity.
ROTATING_PARAMETERS = frozenset(("nonce", "token", "timestamp", "ts", "_"))


def source_key(url):
    parts = urlsplit(str(url))
    pairs = sorted((name, value) for name, value in parse_qsl(parts.query, keep_blank_values=True)
                   if name not in ROTATING_PARAMETERS and name != "mpf_reload")
    return urlunsplit((parts.scheme, parts.netloc, parts.path,
                       urlencode(pairs, quote_via=quote), ""))
