"""Small network helpers: a bounded DNS lookup and an HTTPS connection pinned to an already validated address.

Only what the staging-site reader (admin.py) needs. There is no provider transport here: NailBlog calls no API
model, text and images both go through the Agy worker.
"""
import http.client
import queue
import socket
import ssl
import threading


class NetError(ValueError):
    def __init__(self, code='net_failed'):
        self.code = code
        super().__init__(code)


def resolve(host):
    """getaddrinfo for port 443 with a hard 5 s bound; NetError dns_timeout / invalid_endpoint."""
    result = queue.Queue(maxsize=1)

    def run():
        try:
            result.put(socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM))
        except Exception:
            result.put(None)
    threading.Thread(target=run, daemon=True).start()
    try:
        value = result.get(timeout=5)
    except queue.Empty:
        raise NetError('dns_timeout') from None
    if not value:
        raise NetError('invalid_endpoint')
    return value


class PinnedHTTPS(http.client.HTTPSConnection):
    """Connects to `address` (never re-resolves) while verifying the certificate for `host`."""

    def __init__(self, host, address, timeout):
        super().__init__(host, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        sock = socket.create_connection((self.address, 443), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
