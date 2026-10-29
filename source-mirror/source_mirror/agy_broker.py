"""Restricted local inference bridge. Run separately from the publishing account."""
import argparse
import json
import os
from pathlib import Path
import socket
import struct
import subprocess

MAX_MESSAGE = 2_000_000
MODEL = 'gemini-3.8-flash-medium'
EFFORT = 'medium'


def receive(connection):
    chunks = bytearray()
    while b'\n' not in chunks:
        part = connection.recv(min(65536, MAX_MESSAGE + 1 - len(chunks)))
        if not part:
            raise ValueError('incomplete broker message')
        chunks.extend(part)
        if len(chunks) > MAX_MESSAGE:
            raise ValueError('broker message exceeds limit')
    first, rest = bytes(chunks).split(b'\n', 1)
    if rest:
        raise ValueError('multiple broker messages forbidden')
    return json.loads(first)


def call(socket_path, prompt):
    message = json.dumps({'prompt': prompt}, ensure_ascii=False).encode() + b'\n'
    if len(message) > MAX_MESSAGE:
        raise ValueError('inference request exceeds limit')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(220)
        connection.connect(socket_path)
        connection.sendall(message)
        reply = receive(connection)
    if not isinstance(reply, dict) or reply.get('ok') is not True:
        raise RuntimeError('AGY broker inference unavailable')
    return reply['result']


def infer(config, request):
    if not isinstance(request, dict) or set(request) != {'prompt'}:
        raise ValueError('invalid broker request')
    prompt = request['prompt']
    if not isinstance(prompt, str) or not 1 <= len(prompt) <= 150_000:
        raise ValueError('invalid prompt size')
    binary = config['agy_binary']
    if not Path(binary).is_absolute():
        raise ValueError('AGY binary must be absolute')
    args = [binary, '--model', MODEL, '--effort', EFFORT, '--sandbox',
            '--disable-slash-commands', '--output-format', 'json',
            '--print-timeout', '180s', '--print', prompt]
    result = subprocess.run(args, cwd=config['workdir'], capture_output=True,
                            text=True, timeout=200, check=False)
    if result.returncode != 0 or len(result.stdout.encode()) > MAX_MESSAGE:
        raise RuntimeError('AGY failed or output too large')
    envelope = json.loads(result.stdout)
    if not isinstance(envelope, dict) or envelope.get('status') != 'SUCCESS' or not isinstance(envelope.get('response'), str):
        raise ValueError('invalid AGY success envelope')
    return envelope


def serve(config):
    if os.geteuid() == 0:
        raise ValueError('AGY broker must not run as root')
    allowed_uid = config['allowed_uid']
    if type(allowed_uid) is not int or allowed_uid <= 0:
        raise ValueError('explicit positive worker UID required')
    path = Path(config['socket_path'])
    if not path.is_absolute() or not path.parent.is_dir():
        raise ValueError('private runtime directory required')
    if path.exists() or path.is_symlink():
        if not path.is_socket():
            raise ValueError('refusing non-socket path')
        path.unlink()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(path))
        os.chmod(path, 0o660)
        listener.listen(2)
        while True:
            connection, _ = listener.accept()
            with connection:
                connection.settimeout(220)
                try:
                    _, uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i')))
                    if uid != allowed_uid:
                        raise PermissionError('worker UID mismatch')
                    response = {'ok': True, 'result': infer(config, receive(connection))}
                except Exception as exc:
                    # Deliberately omit auth details, command arguments and model output.
                    response = {'ok': False, 'error': type(exc).__name__}
                try:
                    connection.sendall(json.dumps(response, ensure_ascii=False).encode() + b'\n')
                except OSError:
                    pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    serve(json.loads(Path(args.config).read_text(encoding='utf-8')))


if __name__ == '__main__':
    main()
