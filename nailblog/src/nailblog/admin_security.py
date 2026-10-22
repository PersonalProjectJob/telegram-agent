"""Credential loading and single-use UI write capabilities."""
import json
import os
import stat
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from .publication import AdminError, binding, media_origin


def load_credentials(path):
    try:
        target = Path(path)
        info = target.lstat()
        if not stat.S_ISREG(info.st_mode) or target.is_symlink():
            raise ValueError()
        fd = os.open(target, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'r', encoding='utf-8-sig') as stream:
            actual = os.fstat(stream.fileno())
            if (actual.st_dev, actual.st_ino) != (info.st_dev, info.st_ino):
                raise ValueError()
            if os.name == 'posix' and (stat.S_IMODE(actual.st_mode) != 0o600 or actual.st_uid != os.getuid()):
                raise ValueError()
            result = json.loads(stream.read(16385))
        if (not isinstance(result, dict) or set(result) != {'email', 'password'} or
                any(not isinstance(x, str) or not x.strip() or len(x) > 4096 for x in result.values())):
            raise ValueError()
        return result
    except Exception:
        raise AdminError('credential_file_invalid') from None


class Guard:
    def __init__(self, config):
        binding(config)
        self.config, self.capability, self.blocked = config, None, []
        self.check_action = lambda: None

    @contextmanager
    def allow(self, method, path, predicate, *, count=1, login=False):
        if (self.capability or self.config.mode == 'preview' or
                (not login and (self.config.mode != 'live' or not self.config.contract.verified))):
            raise AdminError('write_not_authorized')
        self.capability = [method, path, predicate, count]
        try:
            yield
        finally:
            self.capability = None

    def permits(self, request):
        u = urlsplit(request.url)
        # No redirected requests with authentication or write payloads, even on the same origin.
        if request.method == 'GET':
            return u.scheme == 'https' and u.netloc in (urlsplit(self.config.contract.site_origin).netloc,
                                                        urlsplit(self.config.contract.api_origin).netloc,
                                                        urlsplit(media_origin(self.config)).netloc)
        cap = self.capability
        if (self.config.mode != 'preview' and request.method == 'OPTIONS' and u.scheme + '://' + u.netloc == self.config.contract.api_origin):
            return True
        if (not cap or request.redirected_from or self.config.mode == 'preview' or u.query or u.fragment
                or u.scheme + '://' + u.netloc != self.config.contract.api_origin
                or (request.method, u.path) != tuple(cap[:2]) or cap[3] <= 0):
            return False
        try:
            valid = cap[2](request)
            if valid:
                self.check_action()
        except Exception:
            valid = False
        if valid:
            cap[3] -= 1
        return bool(valid)

    async def route(self, route):
        if self.permits(route.request):
            await route.continue_()
        else:
            self.blocked.append((route.request.method, urlsplit(route.request.url).netloc,
                                 urlsplit(route.request.url).path))
            await route.abort()
