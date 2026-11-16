"""Offline endpoint, credential and TLS contracts; all network calls are mocked."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from agentfleet_common import ai_transport as transport


class TransportBoundaryTests(unittest.TestCase):
    def test_endpoint_allowlist_and_loopback(self):
        for provider, url in transport.ENDPOINTS.items():
            self.assertEqual(transport.validate_endpoint(provider, url).scheme, 'https')
        for url in ('http://localhost:11434', 'http://127.0.0.1:11434', 'http://[::1]:11434'):
            self.assertEqual(transport.validate_endpoint('ollama', url).scheme, 'http')
        for provider, url in (('deepseek', 'https://evil.example'), ('openai', 'http://api.openai.com/v1'),
                              ('ollama', 'http://example.com'), ('deepseek', 'https://u:p@api.deepseek.com'),
                              ('compatible', 'https://example.com:444'), ('deepseek', 'https://api.deepseek.com?x=1')):
            with self.subTest(provider=provider, url=url), self.assertRaises(transport.ProviderError) as error:
                transport.validate_endpoint(provider, url)
            self.assertEqual(error.exception.code, 'invalid_endpoint')

    def test_compatible_endpoint_rejects_private_dns(self):
        with patch.object(transport, 'resolve', return_value=[(0, 0, 0, '', ('127.0.0.1', 443))]):
            with self.assertRaises(transport.ProviderError) as error:
                transport.validate_endpoint('compatible', 'https://example.com')
            self.assertEqual(error.exception.code, 'invalid_endpoint')
        with patch.object(transport, 'resolve', return_value=[(0, 0, 0, '', ('8.8.8.8', 443))]):
            self.assertEqual(transport.validate_endpoint('compatible', 'https://example.com').hostname, 'example.com')

    def test_read_key_requires_matching_environment_scope(self):
        self.assertEqual(transport.read_key('', 'scope', {'AGENTFLEET_KEY_SCOPE': 'scope', 'AGENTFLEET_API_KEY': 'k'}), 'k')
        self.assertEqual(transport.read_key('', 'scope', {'AGENTFLEET_KEY_SCOPE': 'other', 'AGENTFLEET_API_KEY': 'k'}), '')

    def test_read_key_file_scope_and_unavailable_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'key.json'
            path.write_text(json.dumps({'scope': 'scope', 'key': 'k'}), encoding='utf8')
            path.chmod(0o600)
            self.assertEqual(transport.read_key(path, 'scope', {}), 'k')
            self.assertEqual(transport.read_key(path, 'other', {}), '')
            path.write_text('invalid', encoding='utf8')
            with self.assertRaises(transport.ProviderError) as error:
                transport.read_key(path, 'scope', {})
            self.assertEqual(error.exception.code, 'credentials_unavailable')

    def test_pinned_https_connects_to_address_with_hostname_tls(self):
        raw = Mock()
        context = Mock()
        with patch.object(transport.ssl, 'create_default_context', return_value=context), \
             patch.object(transport.socket, 'create_connection', return_value=raw) as connect:
            connection = transport.PinnedHTTPS('api.example.com', '8.8.8.8', 7)
            connection.connect()
        connect.assert_called_once_with(('8.8.8.8', 443), 7)
        context.wrap_socket.assert_called_once_with(raw, server_hostname='api.example.com')
        self.assertIs(connection.sock, context.wrap_socket.return_value)
