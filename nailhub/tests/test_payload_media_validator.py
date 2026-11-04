import base64
import io
import struct
import unittest
import zlib
import os
import socket
import subprocess
import json
from pathlib import Path
try:
    from PIL import Image
except ImportError:
    Image=None
from nailhub.payload_media_validator import verify, unique_pairs


@unittest.skipIf(Image is None, 'Pillow unavailable in this interpreter')
class MediaVerifierTests(unittest.TestCase):
    def test_valid_image_and_utf8_rights(self):
        stream=io.BytesIO(); Image.new('RGB',(1,1),'white').save(stream,format='PNG')
        encode=lambda value:base64.b64encode(value).decode('ascii')
        result=verify({'images':[encode(stream.getvalue())],'rights':[encode('Quyền ảnh'.encode())]})
        self.assertEqual(result[0]['format'],'PNG')

    def test_invalid_or_oversized_image_and_binary_rights_rejected(self):
        def chunk(tag,data): return struct.pack('>I',len(data))+tag+data+struct.pack('>I',zlib.crc32(tag+data)&0xffffffff)
        ihdr=struct.pack('>IIBBBBB',12000,12000,8,2,0,0,0)
        bomb=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',ihdr)+chunk(b'IDAT',zlib.compress(b'x'))+chunk(b'IEND',b'')
        encode=lambda value:base64.b64encode(value).decode('ascii')
        for image in (bomb,b'invalid'):
            with self.subTest(image=image[:8]), self.assertRaises(Exception):
                verify({'images':[encode(image)],'rights':[encode(b'evidence')]})
        stream=io.BytesIO(); Image.new('RGB',(1,1),'white').save(stream,format='PNG')
        with self.assertRaises(Exception): verify({'images':[encode(stream.getvalue())],'rights':[encode(b'bad\x00value')]})
        with self.assertRaises(ValueError): unique_pairs([('a',1),('a',2)])


@unittest.skipUnless(os.name=='posix' and hasattr(socket,'SO_PEERCRED') and hasattr(os,'geteuid') and os.geteuid()>0,
                     'requires non-root Linux SO_PEERCRED')
class ProcessVerifierTests(unittest.TestCase):
    def run_helper(self, uid):
        script=Path(__file__).resolve().parents[1]/'src'/'nailhub'/'payload_media_validator.py'
        png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')
        request=json.dumps({'images':[base64.b64encode(png).decode()],'rights':[base64.b64encode(b'evidence').decode()]}).encode()
        parent,child=socket.socketpair()
        proc=None
        try:
            proc=subprocess.Popen(['/usr/bin/python3','-I',str(script)],stdin=child,stdout=child,stderr=subprocess.DEVNULL,
                                  env={'SOURCE_MIRROR_REVIEW_ALLOWED_UID':str(uid)},close_fds=True)
            child.close(); parent.settimeout(10)
            chunks=[]
            try:
                parent.sendall(request); parent.shutdown(socket.SHUT_WR)
                while True:
                    part=parent.recv(4096)
                    if not part: break
                    chunks.append(part)
            except (ConnectionResetError,BrokenPipeError):
                pass  # Unauthorized peers may be disconnected without a response.
            raw=b''.join(chunks)
            try: result=json.loads(raw) if raw else None
            except json.JSONDecodeError: result=None
            return proc.wait(timeout=10),result
        finally:
            parent.close(); child.close()
            if proc is not None and proc.poll() is None:
                proc.kill(); proc.wait(timeout=10)

    def test_real_socket_peer_identity_and_process(self):
        code,result=self.run_helper(os.geteuid())
        self.assertEqual(code,0); self.assertTrue(result['ok'])
        code,result=self.run_helper(os.geteuid()+1)
        self.assertNotEqual(code,0); self.assertTrue(result is None or result['ok'] is False)
