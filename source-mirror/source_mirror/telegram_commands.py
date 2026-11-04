"""Owner-only adapter for optional Source Mirror IPC; no NailBlog state access."""
import asyncio
import json
import socket
import struct
from pathlib import Path


def call(path, request):
    sock = Path(path)
    if not sock.is_absolute() or sock.is_symlink() or not sock.is_socket() or not hasattr(socket, 'SO_PEERCRED'):
        raise ValueError('command endpoint unavailable')
    request = json.dumps(request, ensure_ascii=False).encode('utf-8') + b'\n'
    if len(request) > 4096: raise ValueError('request too large')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(7); conn.connect(str(sock))
        _, uid, _ = struct.unpack('3i', conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i')))
        if uid != 0: raise PermissionError('untrusted command endpoint')
        conn.sendall(request); data = bytearray()
        while not data.endswith(b'\n'):
            part = conn.recv(min(1024, 4097 - len(data)))
            if not part or len(data) + len(part) > 4096: raise ValueError('command result unavailable')
            data.extend(part)
    result = json.loads(data.decode('utf-8'))
    if not isinstance(result, dict) or result.get('publication_enabled') is not False or type(result.get('replayed')) is not bool:
        raise ValueError('invalid command result')
    return result


def submit(path, owner_id, message_id, text):
    return call(path, {'owner_id': owner_id, 'message_id': message_id, 'text': text})


async def route(event, owner_ids, socket_path):
    text = getattr(event, 'raw_text', '') or ''
    if not (text.startswith('/source_mirror') or text.startswith('nailblog ')): return False
    sender, chat = getattr(event, 'sender_id', None), getattr(event, 'chat_id', None)
    message = getattr(event, 'message', None)
    message_id = getattr(message, 'id', None)
    if (getattr(event, 'is_private', None) is not True or getattr(event, 'is_group', False)
            or getattr(event, 'is_channel', False) or getattr(event, 'out', None) is not False
            or getattr(event, 'fwd_from', None) is not None or getattr(message, 'fwd_from', None) is not None
            or type(sender) is not int or sender <= 0 or sender != chat or sender not in owner_ids
            or type(message_id) is not int or message_id <= 0): return True
    try:
        user, peer = await event.get_sender(), await event.get_chat()
        if (getattr(user, 'id', None) != sender or getattr(peer, 'id', None) != chat
                or getattr(user, 'bot', None) is not False or getattr(peer, 'bot', None) is not False
                or getattr(user, 'is_self', False) or getattr(peer, 'is_self', False)): return True
        result = await asyncio.to_thread(submit, socket_path, sender, message_id, text)
    except Exception:
        await event.reply('Source Mirror: không xác nhận được kết quả; kiểm tra trạng thái trước khi thử lại.', parse_mode=None, link_preview=False)
        return True
    state = result.get('state')
    if result['replayed']: response = 'Source Mirror: lệnh đã xử lý trước đó; dùng status để xem trạng thái hiện tại.'
    elif state == 'HOLD': response = 'Source Mirror: đã giữ yêu cầu; xử lý URL chưa được kết nối, chưa đăng bài.'
    elif state == 'queued': response = 'Source Mirror: đã nhận URL vào hàng đợi; chưa đăng bài. Dùng status để theo dõi.'
    elif state in ('paused', 'running'):
        response = 'Source Mirror: ' + ('đã tạm dừng slot mới' if state == 'paused' else 'đã tiếp tục')
        if result.get('inflight_may_continue'): response += '; tác vụ đang chạy có thể tiếp tục.'
        if isinstance(result.get('queue'), dict): response += ' Hàng đợi: ' + json.dumps(result['queue'], ensure_ascii=False, sort_keys=True)
        latest=result.get('latest')
        if isinstance(latest,dict):
            response += ' Yêu cầu gần nhất: ' + str(latest.get('state','unknown'))
            if latest.get('state')=='published' and isinstance(latest.get('result_url'),str): response += ' ' + latest['result_url']
    else: response = 'Source Mirror: kết quả chưa rõ; kiểm tra trạng thái.'
    await event.reply(response, parse_mode=None, link_preview=False)
    return True
