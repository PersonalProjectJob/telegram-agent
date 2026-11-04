import hashlib
import json
import struct
import unittest
from unittest.mock import MagicMock,patch
from source_mirror.isolated_media_client import validate,SOCKET

class IsolatedClientTests(unittest.TestCase):
    def run_case(self,uid=0,response=None):
        image=b'image';rights=b'public rights'
        if response is None:
            response={'ok':True,'items':[{'image_sha256':hashlib.sha256(image).hexdigest(),
                'rights_sha256':hashlib.sha256(rights).hexdigest(),'format':'PNG'}]}
        conn=MagicMock();conn.__enter__.return_value=conn;conn.getsockopt.return_value=struct.pack('3i',1,uid,0)
        conn.recv.side_effect=[json.dumps(response).encode(),b'']
        path=MagicMock();path.is_symlink.return_value=False;path.is_socket.return_value=True
        with patch('source_mirror.isolated_media_client.Path',return_value=path), \
             patch('source_mirror.isolated_media_client.socket.SO_PEERCRED',17,create=True), \
             patch('source_mirror.isolated_media_client.socket.AF_UNIX',1,create=True), \
             patch('source_mirror.isolated_media_client.socket.socket',return_value=conn):
            return validate([image],[rights])
    def test_dedicated_socket_and_bound_hashes(self):
        self.assertEqual(SOCKET,'/run/nailhub-source-mirror-media-validator.sock')
        self.assertEqual(self.run_case()[0]['format'],'PNG')
    def test_wrong_peer_and_forged_response_are_held(self):
        with self.assertRaises(PermissionError):self.run_case(uid=123)
        for reply in ({'ok':False},{'ok':True,'items':[{'image_sha256':'a'*64,'rights_sha256':'b'*64,'format':'PNG'}]}):
            with self.subTest(reply=reply),self.assertRaises(ValueError):self.run_case(response=reply)
