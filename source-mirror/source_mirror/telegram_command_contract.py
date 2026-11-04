"""Pure, non-deployed command contract; no Telegram, storage or network access.

An authenticated Telegram adapter and an authenticated IPC server must establish
event identity before invoking this parser. Results never authorize publication.
"""
from urllib.parse import urlsplit


def parse(text, allowed_hosts):
    if not isinstance(text, str) or len(text) > 2200 or not isinstance(allowed_hosts, frozenset):
        raise ValueError('invalid command')
    if text in ('/source_mirror status', '/source_mirror pause', '/source_mirror resume'):
        return {'operation': text.split(' ')[1]}
    prefix = 'nailblog '
    if not text.startswith(prefix):
        raise ValueError('unknown command')
    url = text[len(prefix):]
    if not url or len(url) > 2048 or any(c.isspace() or ord(c) < 32 for c in url):
        raise ValueError('invalid URL')
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.port not in (None, 443)
                or parsed.fragment or parsed.hostname not in allowed_hosts):
            raise ValueError('URL outside source allowlist')
    except ValueError:
        raise ValueError('invalid or unsupported URL') from None
    return {'operation': 'crawl_request', 'url': url, 'publication_enabled': False}
