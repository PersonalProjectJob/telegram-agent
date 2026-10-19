"""The /nailblog namespace: Owner-only parsing, authorization and state-changing handlers.

Messages in the namespace are always consumed (never reach triage), including malformed and
rejected ones. Other namespaces (e.g. /nailhub) and ordinary messages are ignored silently.
"""

import re
from datetime import date, datetime, timedelta

from .config import DEFAULT_SLOTS, DailyConfig
from .schedule import MODES, VN_TZ, iso_slot, parse_hhmm, slot_datetime
from .state import InvalidTransition, SlotTaken
from .trends import format_views, parse_views
from .usage import day_bounds

NAMESPACE = re.compile(r"^\s*/nailblog(?:\b|@)", re.IGNORECASE)
USAGE = ("Lệnh: /nailblog topics | topics ok <số,số> | topics drop <số> | topics edit <số> <chủ đề> | queue | "
         "approve <id> | hold <id> <lý do> | move <id> <YYYY-MM-DD> <06|19> | mode <fixed|random|adaptive> | "
         "pick <06|19> <số> | trend add #tag <lượt xem, vd 12M> | pause | resume | status")
MAX_IDS = 50
MAX_TEXT = 300
NO_ARGS = ("queue", "pause", "resume", "status")


def make_owner_notice_sender(client, verified_owner_ids):
    """Use the active client only, after verify_owner returned its single numeric binding."""
    if len(verified_owner_ids) != 1 or type(verified_owner_ids[0]) is not int or verified_owner_ids[0] <= 0:
        raise ValueError('verified single Owner binding required')
    owner_id = verified_owner_ids[0]
    async def send(text):
        return await client.send_message(owner_id, text, parse_mode=None, link_preview=False)
    return send


async def verify_owner(client, config, me):
    """Bootstrap username only to verify the Owner's numeric binding, never to authorize a DM."""
    if len(config.owner_ids) != 1:
        return ()
    named = await client.get_entity(config.owner_username)
    numeric = await client.get_entity(config.owner_ids[0])
    selected = config.owner_ids[0]
    if (getattr(named, 'id', None) == selected == getattr(numeric, 'id', None)
            and getattr(named, 'bot', None) is False and getattr(numeric, 'bot', None) is False
            and getattr(named, 'username', '').casefold() == config.owner_username.casefold()
            and selected != getattr(me, 'id', None)):
        return (selected,)
    return ()


def _number(token):
    if not token.isascii() or not token.isdigit() or len(token) > 9 or int(token) <= 0:
        return None
    return int(token)


def _free_text(text):
    text = " ".join(text.split())
    if not text or len(text) > MAX_TEXT or any(not c.isprintable() for c in text):
        return None
    return text


def parse(text):
    """Return (name, args) for a well-formed command, otherwise None (caller replies with usage)."""
    parts = text.split()
    if not parts or parts[0] != "/nailblog" or len(parts) < 2:
        return None
    verb, rest = parts[1], parts[2:]
    if verb in NO_ARGS:
        return (verb, ()) if not rest else None
    if verb == "topics":
        if not rest:
            return ("topics", ())
        sub = rest[0]
        if sub == "ok":
            ids = [_number(item) for item in re.split(r"[,\s]+", " ".join(rest[1:]).strip(",")) if item]
            if not ids or None in ids or len(ids) > MAX_IDS:
                return None
            return ("topics_ok", tuple(dict.fromkeys(ids)))
        if sub == "drop" and len(rest) == 2 and _number(rest[1]):
            return ("topics_drop", (_number(rest[1]),))
        if sub == "edit" and len(rest) >= 3 and _number(rest[1]):
            title = _free_text(text.split(None, 4)[4])
            return ("topics_edit", (_number(rest[1]), title)) if title else None
        return None
    if verb == "approve":
        return ("approve", (_number(rest[0]),)) if len(rest) == 1 and _number(rest[0]) else None
    if verb == "hold":
        if len(rest) >= 2 and _number(rest[0]):
            reason = _free_text(text.split(None, 3)[3])
            return ("hold", (_number(rest[0]), reason)) if reason else None
        return None
    if verb == "move":
        if len(rest) == 3 and _number(rest[0]) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", rest[1]) \
                and re.fullmatch(r"\d{2}", rest[2]):
            try:
                day = date.fromisoformat(rest[1])
            except ValueError:
                return None
            return ("move", (_number(rest[0]), day, rest[2] + ":00"))
        return None
    if verb == "pick":
        if len(rest) == 2 and re.fullmatch(r"\d{2}", rest[0]) and _number(rest[1]):
            return ("pick", (rest[0] + ":00", _number(rest[1])))
        return None
    if verb == "trend":
        if len(rest) == 3 and rest[0] == "add" and re.fullmatch(r"#\w{2,40}", rest[1]):
            views = parse_views(rest[2])
            return ("trend_add", (rest[1][1:].casefold(), views)) if views else None
        return None
    if verb == "mode":
        return ("mode", (rest[0],)) if len(rest) == 1 and rest[0] in MODES else None
    return None


def _bounded(lines, limit=3500):
    text = "\n".join(lines)
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _pick(state, slot, number, now, slots, daily):
    """Choose candidate `number` of tomorrow's slot; allowed until daily.pick_deadline of today, and swappable."""
    moment = datetime.fromtimestamp(now, VN_TZ)
    if slot not in slots or slot not in ("06:00", "19:00"):
        return ["Slot phải là một trong: " + ", ".join(s[:2] for s in slots) + "."]
    deadline = datetime.combine(moment.date(), parse_hhmm(daily.pick_deadline), tzinfo=VN_TZ)
    if moment >= deadline:
        return [f"Đã quá hạn chọn ({daily.pick_deadline}): chủ đề ngày mai đã được chốt."]
    target = moment.date() + timedelta(days=1)
    slot_at = iso_slot(slot_datetime(target, slot))
    week = f"daily:{target.isoformat()}"
    candidates = state.topics_for(week, slot_at)
    if not candidates:
        return [f"Chưa có ứng viên cho slot {slot[:2]} ngày mai (mốc {daily.propose_at} chưa chạy)."]
    if number > len(candidates):
        return [f"Slot {slot[:2]} chỉ có {len(candidates)} ứng viên."]
    if any(state.posts_for_topic(row["id"]) for row in candidates):
        return [f"Slot {slot[:2]} đã có bài, không đổi chủ đề được."]
    try:
        topic_id = state.choose_candidate(week, slot_at, number)
    except SlotTaken as error:
        return [f"Slot {slot[:2]} ngày mai đã có chủ đề đã duyệt #{error.holder_id} (từ kế hoạch tuần). "
                f"Loại nó trước (/nailblog topics drop {error.holder_id}) rồi chọn lại."]
    title = state.get_topic(topic_id)["title"]
    return [f"Đã chọn slot {slot[:2]} ngày {target:%d/%m}: #{topic_id} {title[:120]}. "
            f"Chọn lại được trước {daily.pick_deadline}."]


def execute(state, name, args, *, now, config=None):
    """Apply one parsed command to durable state and return the reply messages."""
    slots = tuple(getattr(config, "slots", DEFAULT_SLOTS))
    if name == "topics":
        rows = state.list_topics()
        if not rows:
            return ["Chưa có chủ đề nào."]
        return [_bounded(["Chủ đề:"] + [f"#{r['id']} [{r['state']}] {r['title']}" + (f" ({r['slot_at']})" if r['slot_at'] else "")
                                         for r in rows])]
    if name == "topics_ok":
        done, refused = [], []
        for i in args:
            try:
                if state.set_topic_state(i, "approved", only_from=("proposed",)):
                    done.append(i)
            except SlotTaken as error:
                refused.append(f"#{i} (slot đã có chủ đề đã duyệt #{error.holder_id})")
        reply = f"Đã duyệt chủ đề: {', '.join(map(str, done)) or 'không có'}."
        if refused:
            reply += (" Từ chối: " + ", ".join(refused) + ". Mỗi slot chỉ duyệt một chủ đề; loại chủ đề đã duyệt "
                      "(/nailblog topics drop <số>) nếu muốn đổi.")
        return [reply]
    if name == "topics_drop":
        ok = state.set_topic_state(args[0], "dropped", only_from=("proposed", "approved"))
        return [f"Đã loại chủ đề #{args[0]}." if ok else f"Không loại được chủ đề #{args[0]}."]
    if name == "topics_edit":
        return [f"Đã sửa chủ đề #{args[0]}." if state.edit_topic(*args) else f"Không sửa được chủ đề #{args[0]}."]
    if name == "queue":
        start = datetime.fromtimestamp(day_bounds(now)[0], VN_TZ)
        rows = state.list_posts(slot_from=iso_slot(start), slot_to=iso_slot(start + timedelta(days=2)))
        if not rows:
            return ["Không có bài nào trong 2 ngày tới."]
        return [_bounded(["Hàng đợi 2 ngày:"] + [f"#{r['id']} {r['slot_at']} {r['state']}" for r in rows])]
    if name in ("approve", "hold"):
        target = "scheduled" if name == "approve" else "held"
        try:
            state.transition(args[0], target, reason=args[1] if name == "hold" else "")
        except InvalidTransition as error:
            return [f"Không thực hiện được {name} #{args[0]}: {error}."]
        return [f"Bài #{args[0]}: {'đã lên lịch' if name == 'approve' else 'đã giữ'}."]
    if name == "move":
        post_id, day, slot = args
        if slot not in slots:
            return ["Slot phải là một trong: " + ", ".join(s[:2] for s in slots) + "."]
        moment = slot_datetime(day, slot)
        if moment.timestamp() <= now:
            return ["Slot mới đã qua, không dời được."]
        try:
            state.move_post(post_id, iso_slot(moment))
        except InvalidTransition as error:
            return [f"Không dời được #{post_id}: {error}."]
        return [f"Bài #{post_id} dời sang {iso_slot(moment)}."]
    if name == "trend_add":
        state.add_tiktok_tag(now, args[0], args[1])
        return [f"Đã lưu TikTok #{args[0]}: {format_views(args[1])} lượt xem (tính vào điểm trend 14 ngày)."]
    if name == "pick":
        return _pick(state, args[0], args[1], now, slots, getattr(config, "daily", DailyConfig()))
    if name == "mode":
        state.set_image_mode(args[0])
        return [f"Chế độ lập lịch ảnh: {args[0]}."]
    if name == "pause":
        state.set_paused(True)
        return ["Đã tạm dừng NailBlog."]
    if name == "resume":
        state.set_paused(False)
        return ["Đã chạy lại NailBlog."]
    if name == "status":
        info = state.status()
        spent = state.usd_between(*day_bounds(now))
        limit = getattr(getattr(config, "budget", None), "daily_usd", 3.0)
        posts = ", ".join(f"{k}={v}" for k, v in sorted(info["posts"].items())) or "không có bài"
        return [f"NailBlog: mode={getattr(config, 'mode', 'preview')}, paused={bool(info['paused'])}, "
                f"ảnh={info['image_mode']}, bài: {posts}, chi phí hôm nay ${spent:.2f}/${limit:.2f}, "
                f"kết quả cuối: {info['last_result']}"]
    return [USAGE]


async def route_command(event, service, owner_ids):
    text = getattr(event, "raw_text", "") or ""
    if not NAMESPACE.match(text):
        return False
    # Any message in this namespace is consumed, including malformed and rejected commands.
    message = getattr(event, "message", None)
    sender, peer = getattr(event, "sender_id", None), getattr(event, "chat_id", None)
    if (getattr(event, "is_private", None) is not True or getattr(event, "is_group", False)
            or getattr(event, "is_channel", False) or getattr(event, "out", None) is not False
            or getattr(event, "fwd_from", None) is not None
            or getattr(message, "fwd_from", None) is not None
            or type(sender) is not int or type(peer) is not int or sender != peer
            or sender <= 0 or sender not in owner_ids):
        return True
    # Verify the actual peer and sender are human users (also excludes Saved Messages).
    try:
        user = await event.get_sender()
        chat = await event.get_chat()
        if (getattr(user, "id", None) != sender or getattr(chat, "id", None) != peer
                or getattr(user, "bot", None) is not False or getattr(chat, "bot", None) is not False
                or getattr(user, "is_self", False) or getattr(chat, "is_self", False)):
            return True
    except Exception:
        return True
    parsed = parse(text)
    response = [USAGE] if parsed is None else service.command(*parsed)
    for line in response if isinstance(response, list) else [response]:
        await event.reply(line, parse_mode=None, link_preview=False)
    return True
