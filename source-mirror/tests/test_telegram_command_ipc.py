import json
import os
import socket
import tempfile
import threading
import unittest
from source_mirror.telegram_command_ipc import handle


@unittest.skipUnless(hasattr(socket, 'SO_PEERCRED') and hasattr(os, 'getuid') and os.getuid() > 0, 'Linux non-root peer required')
class IPCTests(unittest.TestCase):
    def test_peer_authorization_and_idempotent_control(self):
        with tempfile.TemporaryDirectory() as root:
            def request(uid, request):
                left, right = socket.socketpair(); errors = []
                def serve():
                    try: handle(left, uid, 42, root)
                    except Exception as exc: errors.append(type(exc).__name__)
                    finally: left.close()
                thread = threading.Thread(target=serve); thread.start()
                try:
                    right.sendall(json.dumps(request).encode() + b'\n')
                    response = right.recv(4096)
                except (ConnectionResetError, BrokenPipeError): response = b''
                finally:
                    right.close(); thread.join(10)
                self.assertFalse(thread.is_alive())
                return response, errors
            data = {'owner_id':42, 'message_id':1, 'text':'/source_mirror resume'}
            response, errors = request(os.getuid(), data)
            self.assertFalse(errors); self.assertEqual(json.loads(response)['state'], 'running')
            response, errors = request(os.getuid(), data)
            self.assertTrue(json.loads(response)['replayed'])
            _, errors = request(os.getuid()+1, data)
            self.assertEqual(errors, ['PermissionError'])
            _, errors = request(os.getuid(), dict(data, owner_id=43))
            self.assertEqual(errors, ['ValueError'])
