"""Gemini image key taken from the service environment: captured once at start, then removed from os.environ so no
child process or later env dump can see it. The value is never logged, printed or put in an exception message."""

import os

from . import gemini_text

GEMINI_IMAGE_ENV = "NAILBLOG_GEMINI_IMAGE_KEY"

_captured = ""


def capture(environ=os.environ):
    """Pop the key out of `environ` into module memory. Idempotent: a later call that finds no variable keeps the
    value captured earlier."""
    global _captured
    value = environ.pop(GEMINI_IMAGE_ENV, None)
    if value:
        _captured = value


def gemini_key_reader(path, scope, group_name=None):
    """Call-compatible with ai_transport.read_key (path and group_name are ignored); the captured key is returned
    for the Gemini scope only."""
    return _captured if scope == gemini_text.KEY_SCOPE else ""
