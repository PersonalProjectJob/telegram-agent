"""Bounded socket-activated command endpoint; disabled unless installed separately."""
import json
import os
import socket
import struct
import sys
from .store import Store
from .telegram_command_backend import execute, notification

LIMIT = 4096


def handle(peer, expected_uid, owner_id, root):
    if type(expected_uid) is not int or expected_uid <= 0 or type(owner_id) is not int or owner_id <= 0:
        raise ValueError('invalid peer policy')
    if not hasattr(socket, 'SO_PEERCRED'): raise ValueError('peer credentials unavailable')
    _, uid, _ = struct.unpack('3i', peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i')))
    if uid != expected_uid: raise PermissionError('peer mismatch')
    peer.settimeout(5)
    data = bytearray()
    while not data.endswith(b'\n'):
        chunk = peer.recv(min(1024, LIMIT + 1 - len(data)))
        if not chunk or len(data) + len(chunk) > LIMIT: raise ValueError('invalid request')
        data.extend(chunk)
    def pairs(items):
        obj = {}
        for key, value in items:
            if key in obj: raise ValueError('duplicate key')
            obj[key] = value
        return obj
    request = json.loads(data.decode('utf-8'), object_pairs_hook=pairs)
    if not isinstance(request, dict) or request.get('owner_id') != owner_id or type(request.get('owner_id')) is not int:
        raise ValueError('invalid request')
    store = Store(root)
    try:
        try:
            if set(request)=={'owner_id','message_id','text'}:
                result = execute(store, owner_id, request['message_id'], request['text'])
            elif set(request)=={'owner_id','operation','message_id'}:
                result = {'notice':notification(store, owner_id, request['operation'], request['message_id']), 'publication_enabled':False, 'replayed':False}
            else: raise ValueError('invalid request')
        except ValueError: result = {'state':'rejected','publication_enabled':False,'replayed':False}
    finally: store.close()
    peer.sendall((json.dumps(result, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8'))


def main():
    try:
        with socket.fromfd(0, socket.AF_UNIX, socket.SOCK_STREAM) as peer:
            handle(peer, int(os.environ['SOURCE_MIRROR_COMMAND_PEER_UID']),
                   int(os.environ['SOURCE_MIRROR_COMMAND_OWNER_ID']), os.environ['SOURCE_MIRROR_COMMAND_ROOT'])
        return 0
    except Exception:
        return 1


if __name__ == '__main__': sys.exit(main())
