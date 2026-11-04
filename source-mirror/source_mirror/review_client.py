"""Explicit opt-in request to the existing tg-nailhub review transport."""
import argparse
import hashlib
import json
from pathlib import Path
import socket


def submit(socket_path, artifact_path, expected_sha):
    path = Path(artifact_path)
    sock = Path(socket_path)
    if (not path.is_absolute() or path != path.resolve() or path.is_symlink()
            or not sock.is_absolute() or sock != sock.resolve() or sock.is_symlink()):
        raise ValueError('canonical paths required')
    data = path.read_bytes()
    if len(data) > 5_000_000 or hashlib.sha256(data).hexdigest() != expected_sha:
        raise ValueError('artifact hash mismatch or size limit')
    raw = data.decode('utf-8')
    request = json.dumps({'artifact': raw, 'sha256': expected_sha}, ensure_ascii=False).encode('utf-8') + b'\n'
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(60)
        connection.connect(str(sock))
        connection.sendall(request)
        response = bytearray()
        while not response.endswith(b'\n'):
            part = connection.recv(4096)
            if not part or len(response) + len(part) > 4096:
                raise ValueError('unknown review transport result')
            response.extend(part)
    result = json.loads(response)
    if result != {'state': 'pending', 'sha256': expected_sha}:
        raise ValueError('review request not confirmed')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket', required=True)
    parser.add_argument('--artifact', required=True)
    parser.add_argument('--sha256', required=True)
    args = parser.parse_args()
    print(json.dumps(submit(args.socket, args.artifact, args.sha256)))


if __name__ == '__main__':
    main()
