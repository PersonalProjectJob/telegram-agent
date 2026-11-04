"""Opt-in Source Mirror Owner review transport; never grants publication rights."""
import asyncio
import base64
import errno
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import stat
import struct
import time
import re
from collections import Counter

from .official_reference import extract, source_title, MAX_BYTES as MAX_HTML_BYTES

MAX_BYTES = 5_000_000
SOURCES = {
    'cdc-niosh-nail-technicians': ('https://www.cdc.gov/niosh/nail-technicians/about/index.html', 'us-workplace-safety', 'US CDC/NIOSH research and workplace safety guidance; not mandatory OSHA law; applicable federal, state and local requirements require separate review', 'CDC/NIOSH'),
    'sba-business-plan': ('https://www.sba.gov/counseling/plan-your-business/', 'us-business', 'General US small-business background; salon context requires human review', 'U.S. Small Business Administration (SBA)'),
}
GLOSSARY = {'nail technician': 'thợ làm móng', 'workplace': 'nơi làm việc', 'ventilation': 'thông gió', 'business plan': 'kế hoạch kinh doanh'}


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate key')
        result[key] = value
    return result


def parse_artifact(raw):
    if not isinstance(raw, str) or not 1 <= len(raw.encode('utf-8')) <= MAX_BYTES:
        raise ValueError('invalid artifact size')
    artifact = json.loads(raw, object_pairs_hook=_object, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('invalid JSON')))
    keys = {'version','status','owner_approval','specialist_review','source','candidate','automated_mqm','generation_envelope','review_envelope','model','effort','glossary','generation_prompt_sha256','review_prompt_sha256'}
    if (not isinstance(artifact, dict) or set(artifact) != keys or artifact.get('version') != 'official-review-v1'
            or artifact.get('status') != 'HOLD' or artifact.get('owner_approval') is not None
            or artifact.get('specialist_review') is not None or artifact.get('model') != 'gemini-3.8-flash-medium' or artifact.get('effort') != 'medium' or artifact.get('glossary') != GLOSSARY):
        raise ValueError('not a held review artifact')
    source_obj = artifact.get('source')
    if not isinstance(source_obj, dict) or set(source_obj) != {'metadata','reference_text','source_label','source_html_base64'} or not isinstance(source_obj['metadata'], dict) or not isinstance(source_obj['reference_text'], str) or not source_obj['reference_text'].strip():
        raise ValueError('invalid source')
    source = source_obj['metadata']
    allowed = SOURCES.get(source.get('source_id'))
    if (not allowed or (source.get('source_url'),source.get('category'),source.get('geographic_scope'),source_obj['source_label']) != allowed
            or source.get('version') != 1 or source.get('status') != 'expert_review_required'
            or source.get('reference_text_sha256') != sha(source_obj['reference_text'])
            or not isinstance(source.get('title'), str) or not source['title'].strip()):
        raise ValueError('source provenance mismatch')
    source_sha = source.get('source_sha256')
    if not isinstance(source_sha, str) or len(source_sha) != 64 or any(c not in '0123456789abcdef' for c in source_sha):
        raise ValueError('invalid source hash')
    encoded = source_obj['source_html_base64']
    if not isinstance(encoded, str) or len(encoded) > ((MAX_HTML_BYTES + 2) // 3) * 4:
        raise ValueError('invalid source HTML size')
    try:
        html = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        raise ValueError('invalid source HTML encoding') from None
    if (len(html) > MAX_HTML_BYTES or base64.b64encode(html).decode('ascii') != encoded
            or hashlib.sha256(html).hexdigest() != source_sha or extract(html) != source_obj['reference_text']
            or source_title(html) != source['title']):
        raise ValueError('source snapshot mismatch')
    candidate, review = artifact['candidate'], artifact['automated_mqm']
    if (not isinstance(candidate, dict) or set(candidate) != {'title_vi','text_vi','uncertainties'} or candidate['uncertainties'] != []
            or any(not isinstance(candidate[k],str) or not candidate[k].strip() or len(candidate[k]) > 150000 or not re.search(r'[ăâđêôơưĂÂĐÊÔƠƯ\u1ea0-\u1ef9]',candidate[k]) for k in ('title_vi','text_vi'))):
        raise ValueError('invalid translation')
    for original, translated in ((source['title'],candidate['title_vi']), (source_obj['reference_text'],candidate['text_vi'])):
        if (original.strip() == translated.strip() or Counter(re.findall(r'\d+(?:[.,]\d+)*',original)) != Counter(re.findall(r'\d+(?:[.,]\d+)*',translated))
                or (re.search(r'\b(?:no|not|never|without|cannot|don.t|mustn.t|shouldn.t)\b',original,re.I) and not re.search(r'\b(?:không|chưa|đừng|chẳng|khỏi)\b',translated,re.I))):
            raise ValueError('translation invariant failed')
    expected = {'verdict','critical','major','minor','issues','uncertainties','glossary_ok','accuracy_ok','technical_clarity_ok'}
    if (not isinstance(review,dict) or set(review) != expected or review['verdict'] != 'PASS'
            or any(type(review[k]) is not int or review[k] != 0 for k in ('critical','major','minor'))
            or review['issues'] != [] or review['uncertainties'] != []
            or any(review[k] is not True for k in ('glossary_ok','accuracy_ok','technical_clarity_ok'))):
        raise ValueError('invalid MQM')
    for key, expected_value in (('generation_envelope', candidate), ('review_envelope', review)):
        envelope = artifact[key]
        if not isinstance(envelope,dict) or set(envelope) != {'status','response'} or envelope.get('status') != 'SUCCESS' or not isinstance(envelope.get('response'),str) or json.loads(envelope['response'],object_pairs_hook=_object) != expected_value:
            raise ValueError('model evidence mismatch')
    for key in ('generation_prompt_sha256','review_prompt_sha256'):
        value = artifact[key]
        if not isinstance(value,str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
            raise ValueError('invalid prompt digest')
    return source_sha


def sha(raw):
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def readable_artifact(raw):
    """Deterministic Owner-facing text; the complete artifact remains private."""
    parse_artifact(raw)
    artifact = json.loads(raw, object_pairs_hook=_object)
    source = artifact['source']; meta = source['metadata']; candidate = artifact['candidate']
    text = ('SOURCE MIRROR — BẢN CHỜ DUYỆT, CHƯA ĐƯỢC PHÉP ĐĂNG\n\n'
            'Nguồn: ' + source['source_label'] + '\nURL: ' + meta['source_url'] +
            '\nPhạm vi: ' + meta['geographic_scope'] + '\nSHA256 nguồn HTML: ' + meta['source_sha256'] +
            '\n\nBẢN TIẾNG VIỆT\n' + candidate['title_vi'] + '\n\n' + candidate['text_vi'] +
            '\n\nNGUYÊN VĂN TIẾNG ANH TRÍCH TỪ MAIN\n' + meta['title'] + '\n\n' + source['reference_text'] +
            '\n\nMQM tự động: PASS; 0 critical, 0 major, 0 minor; không có uncertainty. '
            'Đây là đánh giá tự động, không thay thế chuyên gia.\n'
            'Chuyên gia nội dung, quyền media và Owner: chờ xác thực. Không có quyền đăng.\n')
    data = text.encode('utf-8')
    if len(data) > 1_000_000: raise ValueError('readable review exceeds limit')
    return data


class ReviewTransport:
    def __init__(self, db_path, owner_id, client):
        if type(owner_id) is not int or owner_id <= 0:
            raise ValueError('verified owner required')
        self.owner_id, self.client = owner_id, client
        self.db = sqlite3.connect(db_path, isolation_level=None)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('''CREATE TABLE IF NOT EXISTS source_mirror_reviews (
            digest TEXT PRIMARY KEY, source_sha TEXT NOT NULL, artifact TEXT NOT NULL,
            nonce TEXT NOT NULL UNIQUE, expires INTEGER NOT NULL, state TEXT NOT NULL,
            message_id INTEGER, decision_message_id INTEGER UNIQUE)''')
        # No retry after crash: an interrupted send may have reached Telegram.
        self.db.execute("UPDATE source_mirror_reviews SET state='unknown' WHERE state='sending'")

    async def submit(self, raw, expected_sha):
        source_sha = parse_artifact(raw)
        if not isinstance(expected_sha, str) or sha(raw) != expected_sha:
            raise ValueError('artifact hash mismatch')
        nonce = secrets.token_hex(16)
        expires = int(time.time()) + 86400
        try:
            self.db.execute("INSERT INTO source_mirror_reviews VALUES (?,?,?,?,?,'sending',NULL,NULL)",
                            (expected_sha, source_sha, raw, nonce, expires))
        except sqlite3.IntegrityError:
            raise ValueError('artifact already submitted or unresolved') from None
        try:
            row = self.db.execute('SELECT artifact,source_sha FROM source_mirror_reviews WHERE digest=? AND state=\'sending\'', (expected_sha,)).fetchone()
            if not row or sha(row[0]) != expected_sha or parse_artifact(row[0]) != source_sha:
                raise ValueError('stored artifact changed')
            readable = readable_artifact(row[0])
            readable_sha = hashlib.sha256(readable).hexdigest()
            file = io.BytesIO(readable); file.name = 'source-mirror-review.txt'
            caption = ('Source Mirror — HOLD, cần chuyên gia và Owner duyệt.\n'
                       'SHA256: ' + expected_sha + '\nSource SHA256: ' + source_sha +
                       '\nReadable SHA256: ' + readable_sha +
                       '\nReply: /source-mirror approve ' + nonce + ' hoặc /source-mirror reject ' + nonce +
                       '\nHết hạn UTC epoch: ' + str(expires))
            message = await self.client.send_file(self.owner_id, file, caption=caption, parse_mode=None)
            if type(getattr(message, 'id', None)) is not int or message.id <= 0:
                raise ValueError('send outcome unknown')
            self.db.execute("UPDATE source_mirror_reviews SET state='pending',message_id=? WHERE digest=? AND state='sending'", (message.id, expected_sha))
        except BaseException:
            self.db.execute("UPDATE source_mirror_reviews SET state='unknown' WHERE digest=? AND state='sending'", (expected_sha,))
            raise
        return {'state': 'pending', 'sha256': expected_sha}

    async def handle(self, event):
        text = getattr(event, 'raw_text', '') or ''
        if not text.startswith('/source-mirror '):
            return False
        message = getattr(event, 'message', None)
        sender, peer = getattr(event, 'sender_id', None), getattr(event, 'chat_id', None)
        if (getattr(event, 'is_private', None) is not True or getattr(event, 'out', None) is not False
                or sender != self.owner_id or peer != self.owner_id or getattr(event, 'fwd_from', None) is not None
                or getattr(message, 'fwd_from', None) is not None or getattr(message, 'edit_date', None) is not None):
            return True
        try:
            user, chat = await event.get_sender(), await event.get_chat()
            if any(getattr(entity, 'id', None) != self.owner_id or getattr(entity, 'bot', None) is not False or getattr(entity, 'is_self', False) for entity in (user, chat)):
                return True
            parts = text.split()
            if len(parts) != 3 or parts[1] not in ('approve', 'reject') or len(parts[2]) != 32:
                return True
            reply = getattr(message, 'reply_to_msg_id', None)
            msg_id = getattr(message, 'id', None)
            if type(reply) is not int or type(msg_id) is not int or msg_id <= 0:
                return True
            row = self.db.execute("SELECT digest,source_sha,artifact,expires FROM source_mirror_reviews WHERE nonce=? AND message_id=? AND state='pending'", (parts[2], reply)).fetchone()
            if not row or row[3] <= int(time.time()) or sha(row[2]) != row[0] or parse_artifact(row[2]) != row[1]:
                return True
            original = await self.client.get_messages(self.owner_id, ids=reply)
            if original is None or getattr(original, 'edit_date', None) is not None or getattr(original, 'out', None) is not True:
                return True
            # Exact caption ties the Owner's reply to both digest and nonce.
            caption = getattr(original, 'raw_text', '') or ''
            readable = readable_artifact(row[2])
            readable_sha = hashlib.sha256(readable).hexdigest()
            if ('SHA256: ' + row[0] + '\nSource SHA256: ' + row[1] + '\nReadable SHA256: ' + readable_sha + '\nReply: /source-mirror approve ' + parts[2]) not in caption:
                return True
            attachment = await self.client.download_media(original, file=bytes)
            if not isinstance(attachment, bytes) or attachment != readable:
                return True
            current = await self.client.get_messages(self.owner_id, ids=msg_id)
            if (current is None or getattr(current, 'edit_date', None) is not None
                    or getattr(current, 'raw_text', None) != text or getattr(current, 'reply_to_msg_id', None) != reply
                    or getattr(current, 'fwd_from', None) is not None):
                return True
            self.db.execute("UPDATE source_mirror_reviews SET state=?,decision_message_id=? WHERE digest=? AND state='pending' AND expires>?", (parts[1], msg_id, row[0], int(time.time())))
        except (Exception,):
            return True
        return True

    def close(self):
        self.db.close()


async def start_server(path, allowed_uid, transport):
    path = Path(path)
    if (not path.is_absolute() or path != path.resolve() or type(allowed_uid) is not int or allowed_uid <= 0
            or not hasattr(socket, 'SO_PEERCRED')):
        raise ValueError('invalid review socket configuration')
    parent = path.parent.stat()
    if parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) & 0o022:
        raise ValueError('unsafe review socket directory')
    if path.exists() or path.is_symlink():
        existing = path.lstat()
        if not stat.S_ISSOCK(existing.st_mode) or existing.st_uid != os.geteuid():
            raise ValueError('unsafe existing socket')
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.settimeout(0.2)
            result = probe.connect_ex(str(path))
        finally:
            probe.close()
        if result != errno.ECONNREFUSED or path.lstat().st_ino != existing.st_ino:
            raise ValueError('review socket may be active')
        path.unlink()
    async def connected(reader, writer):
        try:
            peer = writer.get_extra_info('socket')
            _, uid, _ = struct.unpack('3i', peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i')))
            if uid != allowed_uid:
                raise PermissionError('peer UID mismatch')
            line = await asyncio.wait_for(reader.readline(), timeout=10)
            if len(line) > MAX_BYTES * 6 or not line.endswith(b'\n'):
                raise ValueError('invalid request')
            request = json.loads(line, object_pairs_hook=_object)
            if not isinstance(request, dict) or set(request) != {'artifact', 'sha256'}:
                raise ValueError('invalid request')
            response = await transport.submit(request['artifact'], request['sha256'])
        except Exception:
            response = {'state': 'HOLD', 'error': 'review request failed'}
        try:
            writer.write((json.dumps(response) + '\n').encode()); await writer.drain()
        finally:
            writer.close(); await writer.wait_closed()
    active = set()
    async def tracked(reader, writer):
        task = asyncio.current_task()
        active.add(task)
        try:
            await connected(reader, writer)
        finally:
            active.discard(task)
    server = await asyncio.start_unix_server(tracked, path=str(path), limit=MAX_BYTES * 6 + 1)
    server.review_tasks = active
    os.chmod(path, 0o660)
    return server
