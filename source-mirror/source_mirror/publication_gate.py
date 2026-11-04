"""Publication boundary for sources without a verified publishing contract.

Until automated factual and rights checks, payload/media binding and the
official publishing contract exist, government sources remain held.
"""
from urllib.parse import urlsplit


def require_supported_source(url):
    if not isinstance(url, str):
        raise ValueError('invalid publication source')
    parsed = urlsplit(url)
    host = (parsed.hostname or '').rstrip('.').lower()
    if parsed.scheme not in ('http', 'https') or not host or parsed.username or parsed.password:
        raise ValueError('invalid publication source')
    if host == 'gov' or host.endswith('.gov'):
        raise ValueError('official source requires verified factual, media rights and publication checks')
