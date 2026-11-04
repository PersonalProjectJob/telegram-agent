import hashlib
import json
import struct
import socket
import unittest
from unittest.mock import MagicMock, patch
from nailhub.payload_media_client import validate


class ValidatorClientTests(unittest.TestCase):
    def run_client(self, response, uid=0):
        conn=MagicMock(); conn.__enter__.return_value=conn
        conn.getsockopt.return_value=struct.pack('3i',1,uid,1)
        conn.recv.side_effect=response if isinstance(response,Exception) else [response,b'']
        with patch('nailhub.payload_media_client.Path.is_socket',return_value=True), \
             patch('nailhub.payload_media_client.Path.is_symlink',return_value=False), \
             patch('nailhub.payload_media_client.socket.SO_PEERCRED',17,create=True), \
             patch('nailhub.payload_media_client.socket.AF_UNIX',1,create=True), \
             patch('nailhub.payload_media_client.socket.socket',return_value=conn):
            return validate([b'image'],[b'rights'])

    def test_valid_reply_requires_exact_digests(self):
        item={'image_sha256':hashlib.sha256(b'image').hexdigest(),'rights_sha256':hashlib.sha256(b'rights').hexdigest(),'format':'PNG'}
        self.assertEqual(self.run_client(json.dumps({'ok':True,'items':[item]}).encode()),[item])
        for response in (b'',b'{}',b'{"ok":false,"ok":true,"items":[]}',json.dumps({'ok':True,'items':[dict(item,image_sha256='a'*64)]}).encode()):
            with self.subTest(response=response), self.assertRaises(ValueError): self.run_client(response)
        with self.assertRaises(PermissionError): self.run_client(json.dumps({'ok':True,'items':[item]}).encode(),uid=123)

    def test_oversize_before_connection(self):
        with self.assertRaises(ValueError): validate([b'x'*4_500_001],[b'rights'])

    def test_timeout_truncated_and_oversized_response_fail_closed(self):
        with self.assertRaises(socket.timeout): self.run_client(socket.timeout('timed out'))
        for response in (b'{"ok":true',b'x'*8193):
            with self.subTest(length=len(response)), self.assertRaises(ValueError): self.run_client(response)
