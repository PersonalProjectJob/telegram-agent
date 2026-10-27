"""The /nailblog namespace: Owner-only parsing, authorization and state-changing handlers.

Messages in the namespace are always consumed (never reach triage), including malformed and
rejected ones. Other namespaces (e.g. /nailhub) and ordinary messages are ignored silently.
"""

import asyncio
import json
import logging
import os
import re
from datetime import date, datetime, timedelta

from .config import CATEGORIES, DEFAULT_SLOTS, DailyConfig
from .schedule import MODES, VN_TZ, iso_slot, parse_hhmm, slot_datetime
from .paid import local_ledger_problem
from .state import InvalidTransition, SlotTaken, State, _file_digest, startup_binding
from .trends import format_views, parse_views
from .usage import day_bounds

LOG = logging.getLogger(__name__)
NAMESPACE = re.compile(r"^\s*/nailblog(?:\b|@)", re.IGNORECASE)
USAGE = ("Lệnh: /nailblog topics | topics ok <số,số> | topics drop <số> | topics edit <số> <chủ đề> | queue | "
         "approve <id> | hold <id> <lý do> | move <id> <YYYY-MM-DD> <giờ> | mode <fixed|random|adaptive> | "
         "pick <giờ> <số> | trend add #tag <lượt xem, vd 12M> | ok <id> | reject <id> <lý do> | flow | "
         "pause | resume | status")
MAX_IDS = 50
_NO_BINDING = object()
MAX_TEXT = 300
NO_ARGS = ("queue", "pause", "resume", "status", "flow")


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
    if verb == "ok":
        return ("ok", (_number(rest[0]),)) if len(rest) == 1 and _number(rest[0]) else None
    if verb == "reject":
        if len(rest) == 1 and _number(rest[0]):
            return ("reject", (_number(rest[0]), ""))   # the store answers `reason_required`
        if len(rest) >= 2 and _number(rest[0]):
            reason = _free_text(text.split(None, 3)[3])
            return ("reject", (_number(rest[0]), reason)) if reason else None
        return None
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


def _autoflow_run_for_topic(state, topic_id):
    """The autoflow run row of a topic, else None (no table yet means no run)."""
    with state.connection() as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='autoflow_runs'").fetchone() is None:
            return None
        row = db.execute("SELECT * FROM autoflow_runs WHERE topic_id=?", (topic_id,)).fetchone()
        return dict(row) if row else None


def _autoflow_run_for_slot(state, slot_at):
    """Any autoflow run whose slot is `slot_at` (compared as instants), else None."""
    try:
        wanted = datetime.fromisoformat(slot_at).timestamp()
    except ValueError:
        return None
    with state.connection() as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='autoflow_runs'").fetchone() is None:
            return None
        for row in db.execute("SELECT * FROM autoflow_runs"):
            try:
                if datetime.fromisoformat(row["slot_at"]).timestamp() == wanted:
                    return dict(row)
            except ValueError:
                continue
    return None


def _post_in_a_run(state, post):
    """True when the post belongs to a one-post run or an autoflow run (their approvals are snapshot-bound)."""
    with state.connection() as db:
        names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "onepost_runs" in names and db.execute("SELECT 1 FROM onepost_runs WHERE post_id=?",
                                                  (post["id"],)).fetchone():
            return True
        return bool("autoflow_runs" in names and post.get("topic_id") is not None
                    and db.execute("SELECT 1 FROM autoflow_runs WHERE topic_id=?", (post["topic_id"],)).fetchone())


def _pick(state, slot, number, now, slots, daily):
    """Choose candidate `number` of tomorrow's slot; allowed until daily.pick_deadline of today, and swappable."""
    moment = datetime.fromtimestamp(now, VN_TZ)
    if slot not in slots:
        return ["Slot phải là một trong: " + ", ".join(s[:2] for s in slots) + "."]
    deadline = datetime.combine(moment.date(), parse_hhmm(daily.pick_deadline), tzinfo=VN_TZ)
    if moment >= deadline:
        return [f"Đã quá hạn chọn ({daily.pick_deadline}): chủ đề ngày mai đã được chốt."]
    target = moment.date() + timedelta(days=1)
    slot_at = iso_slot(slot_datetime(target, slot))
    week = f"daily:{target.isoformat()}"
    if _autoflow_run_for_slot(state, slot_at) is not None:
        return [f"Slot {slot[:2]} ngày mai đã có run autoflow (đã trả phí/đang chạy): không đổi chủ đề được."]
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


STAGE_TEXT = {
    "new": ("mới tạo", "tạo bài và ảnh (trả phí, trong trần của run)"),
    "images_send": ("ảnh đã sẵn sàng", "gửi thẻ duyệt ảnh cho Owner"),
    "images_wait": ("chờ Owner duyệt ảnh", None),
    "images_retry": ("đang tạo lại ảnh (tối đa một lần)", "gửi thẻ ảnh mới"),
    "saving": ("ảnh đã duyệt", "lưu Draft lên admin (không lưu trong 20 phút trước slot)"),
    "draft_send": ("Draft đã lưu", "gửi thẻ duyệt Draft cho Owner"),
    "draft_wait": ("chờ Owner duyệt Draft", None),
    "scheduled": ("đã lên lịch", "scheduler đăng đúng giờ slot"),
    "published": ("đã đăng", "xong"),
    "held": ("giữ lại", "cần Owner kiểm tra, autoflow không tự thử lại"),
    "skipped": ("bỏ qua", "không còn bước nào"),
}
WAIT_KEY = {"images_wait": "images", "draft_wait": "draft"}


def flow_line(row, now=None):
    """One Vietnamese line for an `autoflow_runs` row: stage and the next step. Only sanitized codes are shown."""
    label, step = STAGE_TEXT.get(row["stage"], (row["stage"], "kiểm tra thủ công"))
    if step is None:
        try:
            request = (json.loads(row["ctx"] or "{}").get(WAIT_KEY[row["stage"]]) or {}).get("id")
        except ValueError:
            request = None
        ident = request if request is not None else "<id>"
        step = f"/nailblog ok {ident} hoặc /nailblog reject {ident} <lý do>"
    line = f"#{row['topic_id']} slot {row['slot_at']} [{row['stage']}] {label}; tiếp theo: {step}"
    if row["terminal_reason"]:
        line += f" (lý do: {row['terminal_reason']})"
    if row["failures"]:
        line += f" (lỗi liên tiếp: {row['failures']}"
        if now is not None and row["next_attempt_at"] > now:
            line += ", thử lại " + datetime.fromtimestamp(row["next_attempt_at"], VN_TZ).strftime("%H:%M %d/%m")
        line += ")"
    return line


def flow_lines(state, now=None):
    from .autoflow import AutoflowRuns
    return [flow_line(row, now) for row in AutoflowRuns(state).rows()]


DECISION_TEXT = {
    "recorded": "đã ghi nhận",
    "already_decided": "đã được quyết định như vậy từ trước, không đổi gì",
    "wrong_sender": "từ chối: người gửi không phải Owner đã xác minh",
    "owner_unbound": "từ chối: chưa xác minh được Owner",
    "unknown_request": "không có yêu cầu duyệt này",
    "reason_required": "cần lý do: /nailblog reject <id> <lý do>",
    "already_decided_differently": "đã được quyết định khác trước đó, không đổi được",
    "superseded": "đã bị thay bằng yêu cầu mới, hãy dùng thẻ mới nhất",
    "expired": "đã hết hạn duyệt",
}


def decide_approval(state, verdict, request_id, reason, *, sender_id, owner_ids, now):
    """`/nailblog ok|reject`: record the Owner's decision only (the engine acts on it at its next pass)."""
    from .approvals import ApprovalStore
    owner = owner_ids[0] if len(owner_ids) == 1 else None
    result = ApprovalStore(state).decide(request_id, verdict, reason, sender_id, owner, now)
    text = DECISION_TEXT.get(result["code"], "không xử lý được")
    word = "OK" if verdict == "ok" else "từ chối"
    reply = f"Yêu cầu duyệt #{request_id} ({word}): {text} [{result['code']}]."
    if result["code"] == "recorded":
        reply += " Autoflow sẽ chạy bước tiếp theo ở lượt kiểm tra kế tiếp (khoảng một phút)."
    return [reply]


async def _check_gemini(config):
    """Key captured + ListModels for the configured models. Returns failed check codes."""
    from .images import ImageError
    from .images import provider as gemini
    if config.image.provider != "gemini":
        return []
    try:
        client = gemini.GeminiImages(config)
        client.headers()
    except Exception:
        return ["gemini_key_missing"]
    try:
        await asyncio.to_thread(client.verify_models)
    except ImageError as error:
        code = str(error) if re.fullmatch(r"[a-z_]{3,40}", str(error)) else "image_error"
        return ["gemini_models_unavailable:" + code]
    except Exception as error:
        return ["gemini_models_unavailable:" + type(error).__name__]
    return []


async def _check_admin(service):
    """Contract report verified + the admin session can open (read-only). Returns failed check codes."""
    from .admin import Admin
    config = service.config
    failed = []
    try:
        Admin(config).write_ready()
    except Exception:
        failed.append("contract_unverified")
    try:
        async with service.browser_lock:
            async with asyncio.timeout(config.admin.publish_timeout_seconds):
                async with Admin(config) as admin:
                    await admin.ready()
    except Exception as error:
        LOG.warning("NailBlog resume preflight admin failed exception=%s", type(error).__name__)
        failed.append("admin_not_ready")
    return failed


def _check_budget(service):
    from .paid import PaidLedger
    from .usage import BudgetExceeded
    try:
        PaidLedger(service.state, service.config, service.clock).check()
    except BudgetExceeded:
        return ["budget_exhausted"]
    except Exception:
        return ["budget_unknown"]
    return []


async def preflight(service, *, config_path=None):
    """Failed check codes before arming; empty means safe to arm. Live mode checks the contract, the admin session
    and the remaining budget; `autoflow.enabled` adds the Gemini key, ListModels and the category GUIDs."""
    config = service.config
    path = config_path if config_path is not None else os.environ.get("NAILBLOG_CONFIG", "")
    failed = []
    if not path or _file_digest(path) == "missing":
        failed.append("config_digest_missing")
    if local_ledger_problem(service.state, config, service.clock):
        failed.append("local_ledger_not_empty")   # same-day spend in the root's own DB would be forgotten
    if config.mode == "live":
        failed += await _check_admin(service)
        failed += _check_budget(service)
    if config.autoflow.enabled:
        if config.image.provider == 'subscription':
            try:
                from .images.subscription import SubscriptionImages
                SubscriptionImages(config).verify_models()
            except Exception:
                failed.append('subscription_queue_unavailable')
        else:
            failed += await _check_gemini(config)
        if not set(CATEGORIES) <= set(config.admin.category_ids):
            failed.append("category_ids_incomplete")
    return failed


async def resume(service, *, config_path=None):
    """`/nailblog resume`: preflight, then arm the current binding and unpause. On any failure nothing changes."""
    loaded = getattr(service, "startup_binding", _NO_BINDING)   # computed once when this process started (None = failed)
    path = config_path if config_path is not None else os.environ.get("NAILBLOG_CONFIG", "")
    failed = await preflight(service, config_path=path)
    current = startup_binding(service.config, config_path=path)[0]
    if loaded is None:
        failed.append("binding_unavailable")   # fail closed: never arm what this process could not bind
    elif loaded is not _NO_BINDING and loaded != current:
        failed.append("restart_required")   # build/config/mode on disk differ from what this process loaded
    if failed:
        return ["Chưa chạy lại NailBlog: preflight lỗi (" + ", ".join(failed) + "). Vẫn giữ trạng thái tạm dừng/chưa arm."]
    service.state.arm(current if loaded is _NO_BINDING else loaded)   # arm exactly what this process loaded
    service.state.set_paused(False)
    return ["Đã chạy lại NailBlog (preflight đạt, đã arm)."]


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
        if _autoflow_run_for_topic(state, args[0]) is not None:
            return [f"Không loại được chủ đề #{args[0]}: slot của nó đã có run autoflow (đã trả phí/đang chạy). "
                    "Dùng /nailblog flow để xem; run giữ lại hoặc bỏ qua sẽ tự kết thúc."]
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
        if name == "approve":
            post = state.get_post(args[0])
            if getattr(getattr(config, "autoflow", None), "enabled", False) or (post and _post_in_a_run(state, post)):
                return [f"Không duyệt #{args[0]} bằng approve: bài thuộc luồng one-post/autoflow, chỉ duyệt được bằng "
                        "/nailblog ok <id> theo đúng thẻ Draft (gắn với snapshot)."]
        try:
            if name == "approve" and getattr(config, 'mode', 'preview') == 'live':
                from .admin_state import AdminStore
                from .publication import AdminError
                try:
                    AdminStore(state, config).approve(args[0], now)
                except AdminError as error:
                    return [f"Không duyệt được #{args[0]}: {error}."]
            else:
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
    if name == "flow":
        rows = flow_lines(state, now)
        head = "Autoflow" + ("" if getattr(getattr(config, "autoflow", None), "enabled", False) else " (đang tắt)") + ":"
        return [_bounded([head] + rows)] if rows else [head + " chưa có run nào."]
    if name == "status":
        info = state.status()
        shared = getattr(getattr(config, "budget", None), "ledger_db_path", "")
        spent = (State(shared) if shared else state).usd_between(*day_bounds(now))   # the cap's ledger, not just this root
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
    if parsed is None:
        response = [USAGE]
    elif parsed[0] in ("ok", "reject"):
        response = decide_approval(service.state, parsed[0], parsed[1][0], parsed[1][1] if parsed[0] == "reject" else "",
                                   sender_id=sender, owner_ids=owner_ids, now=service.clock())
    elif parsed[0] == "resume":
        response = await resume(service)
    else:
        response = service.command(*parsed)
    for line in response if isinstance(response, list) else [response]:
        await event.reply(line, parse_mode=None, link_preview=False)
    return True
