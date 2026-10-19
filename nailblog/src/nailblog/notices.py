"""Owner DMs with duplicate protection: each notice key is delivered at most once, retried after failures."""

import hashlib
import json
import logging
import time

LOG = logging.getLogger(__name__)
VERSION = 1
MAX_TEXT = 3500


def notice_key(kind, ref, version=VERSION):
    return hashlib.sha256(json.dumps([version, kind, str(ref)], ensure_ascii=False).encode()).hexdigest()


def uncertain_notice(row):
    """DM for a post whose publish outcome is unknown. Carries ids and slot only, never draft content."""
    if row.get("state") != "uncertain":
        return None
    key = notice_key("uncertain", f"{row['id']}|{row['slot_at']}|{row.get('reason', '')}")
    return key, (f"NailBlog: bài #{row['id']} (slot {row['slot_at']}) chưa xác định được kết quả đăng. "
                 "Kiểm tra trang quản trị, không đăng lại. Dùng /nailblog hold <id> <lý do> nếu muốn giữ.")


async def send_once(state, send, key, text, now=None):
    """Send `text` unless `key` was already delivered or is backing off; return True when delivered now."""
    now = time.time() if now is None else now
    if not state.notice_due(key, now):
        return False
    try:
        await send(text[:MAX_TEXT])
    except Exception as error:
        state.notice_failed(key, now)
        LOG.warning("NailBlog notice send failed exception=%s", type(error).__name__)
        return False
    state.notice_sent(key, now)
    return True
