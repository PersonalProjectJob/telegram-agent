"""Owner approval requests (text commands only) and the Telegram preview of what is being approved.

Handoff notes for callers (S8/S9):
- The bypass warning line `ảnh chưa qua model chấm (bypass)` is printed ONLY when the artifact carries
  `review_bypass` (truthy, and `enabled` not false). Pass the one-post `artifact()` through unchanged.
- Never compare approval state by hand: use `ApprovalStore.is_approved(run_id, kind, snapshot)`.
- `request()` raises ApprovalError('snapshot_superseded') for a snapshot that a newer one replaced.
"""
import base64
import html
import io
import json
from datetime import datetime, timedelta, timezone

from .override_policy import NEVER_OVERRIDABLE

KINDS = ('images', 'draft')
VN_TZ = timezone(timedelta(hours=7))
DASH = '—'
MAX_REASON = 500
MAX_ISSUES = 20
AUTO_BY = 'auto'
AUTO_REASON = 'owner_approval=false (D-034)'
CARD_LIMIT = 4096  # Telegram text message limit


class ApprovalError(ValueError):
    pass


def _result(ok, code, row=None):
    return {'ok': ok, 'code': code, 'id': row['id'] if row else None,
            'status': row['status'] if row else None, 'reason': row['reason'] if row else None,
            'requires_override': bool(row['requires_override']) if row else None,
            'override': bool(row['override']) if row else None}


def _issue_list(issues):
    """Normalize held-card issues (dicts, objects with code/message/overridable/kind, or plain strings) to
    [{'code','message','overridable','kind'}]. `overridable` is True only when the source says exactly True."""
    out = []
    for item in list(issues or ())[:MAX_ISSUES]:
        if isinstance(item, dict):
            code, message, overridable, kind = (item.get('code'), item.get('message'), item.get('overridable'),
                                                item.get('kind'))
        elif isinstance(item, str):
            code, message, overridable, kind = item, '', False, ''
        else:
            code, message, overridable, kind = (getattr(item, 'code', None), getattr(item, 'message', None),
                                                getattr(item, 'overridable', None), getattr(item, 'kind', None))
        out.append({'code': str(code or '?')[:60], 'message': ' '.join(str(message or '').split())[:200],
                    'overridable': overridable is True, 'kind': str(kind or '')[:20]})
    return out


def _blocks_override(issue):
    """True for an issue the Owner may never override (facts/Han/medical claim, a hold, or not marked overridable)."""
    return (not isinstance(issue, dict) or issue.get('overridable') is not True
            or issue.get('code') in NEVER_OVERRIDABLE or issue.get('kind') == 'hold')


class ApprovalStore:
    def __init__(self, state):
        self.state = state
        with state.connection() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS approval_requests(
                id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                kind TEXT NOT NULL CHECK(kind IN ('images','draft')), snapshot TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending','approved','rejected','expired','superseded')),
                reason TEXT, created_at REAL NOT NULL, deadline REAL NOT NULL, decided_at REAL, decided_by INTEGER,
                message_ids TEXT NOT NULL DEFAULT '[]', UNIQUE(run_id, kind, snapshot))''')
            have = {row['name'] for row in db.execute('PRAGMA table_info(approval_requests)')}
            for column, ddl in (('requires_override', 'INTEGER NOT NULL DEFAULT 0'),
                                ('issues', "TEXT NOT NULL DEFAULT '[]'"),
                                ('override', 'INTEGER NOT NULL DEFAULT 0')):
                if column not in have:   # old DBs stay readable: new columns default to 0 / '[]'
                    db.execute(f'ALTER TABLE approval_requests ADD COLUMN {column} {ddl}')

    def request(self, kind, run_id, snapshot, deadline, now, *, requires_override=False, issues=None):
        if kind not in KINDS:
            raise ApprovalError('invalid_kind')
        if not isinstance(run_id, str) or not run_id or not isinstance(snapshot, str) or not snapshot:
            raise ApprovalError('invalid_request')
        issue_rows = _issue_list(issues)
        if any(_blocks_override(issue) for issue in issue_rows):
            raise ApprovalError('not_overridable')   # layer 1: no row is ever created for these
        with self.state.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            same = db.execute('SELECT id, status, requires_override FROM approval_requests '
                              'WHERE run_id=? AND kind=? AND snapshot=?', (run_id, kind, snapshot)).fetchone()
            if same:
                if same['status'] == 'superseded':
                    raise ApprovalError('snapshot_superseded')
                if same['status'] == 'pending' and requires_override and not same['requires_override']:
                    db.execute('UPDATE approval_requests SET requires_override=1, issues=? WHERE id=?',
                               (json.dumps(issue_rows, ensure_ascii=False), same['id']))   # raise only, never lower
                return same['id']
            db.execute("UPDATE approval_requests SET status='superseded' WHERE run_id=? AND kind=? AND status='pending'",
                       (run_id, kind))
            return db.execute('INSERT INTO approval_requests(run_id,kind,snapshot,status,created_at,deadline,'
                              "requires_override,issues) VALUES(?,?,?,'pending',?,?,?,?)",
                              (run_id, kind, snapshot, now, deadline, 1 if requires_override else 0,
                               json.dumps(issue_rows, ensure_ascii=False))).lastrowid

    def decide(self, request_id, verdict, reason, sender_id, owner_id, now, *, override=False):
        """Record only; never publishes. Returns {'ok','code','id','status','reason'}; refusals change nothing
        (except marking a pending request past its deadline as expired)."""
        if verdict not in ('ok', 'reject'):
            raise ApprovalError('invalid_verdict')
        want = 'approved' if verdict == 'ok' else 'rejected'
        reason = ' '.join(reason.split()) if isinstance(reason, str) else ''
        with self.state.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM approval_requests WHERE id=?', (request_id,)).fetchone()
            if type(owner_id) is not int or owner_id <= 0:
                return _result(False, 'owner_unbound')
            if type(sender_id) is not int or sender_id != owner_id:
                return _result(False, 'wrong_sender')
            if row is None:
                return _result(False, 'unknown_request')
            if verdict == 'reject' and not reason:
                return _result(False, 'reason_required', row)
            if row['status'] in ('approved', 'rejected'):
                if row['status'] == want:
                    return _result(True, 'already_decided', row)
                return _result(False, 'already_decided_differently', row)
            if row['status'] == 'superseded':
                return _result(False, 'superseded', row)
            if row['status'] == 'expired':
                return _result(False, 'expired', row)
            if now > row['deadline']:
                db.execute("UPDATE approval_requests SET status='expired' WHERE id=? AND status='pending'", (request_id,))
                return _result(False, 'expired', db.execute('SELECT * FROM approval_requests WHERE id=?',
                                                            (request_id,)).fetchone())
            overriding = verdict == 'ok' and bool(override)
            if overriding:   # layer 2: re-check what is stored, whatever the request() of that day allowed
                try:
                    stored = json.loads(row['issues'] or '[]')
                except ValueError:
                    stored = [None]
                if not isinstance(stored, list) or any(_blocks_override(issue) for issue in stored):
                    return _result(False, 'override_forbidden', row)
            if verdict == 'ok' and row['requires_override']:
                if not overriding:
                    return _result(False, 'override_required', row)
                if not reason:
                    return _result(False, 'reason_required', row)
            flagged = overriding and bool(row['requires_override'])   # a normal request is a plain ok
            keep_reason = verdict == 'reject' or (flagged and bool(reason))
            db.execute('UPDATE approval_requests SET status=?, reason=?, decided_at=?, decided_by=?, override=? '
                       'WHERE id=?', (want, reason[:MAX_REASON] if keep_reason else None, now, sender_id,
                                      1 if flagged else 0, request_id))
            return _result(True, 'recorded', db.execute('SELECT * FROM approval_requests WHERE id=?',
                                                        (request_id,)).fetchone())

    def auto_approve(self, kind, run_id, snapshot, deadline, now, *, quality, requires_override=False, issues=None):
        """Machine approval (D-034, `autoflow.owner_approval = false`): writes ONE verified `approved` row bound to this
        exact (run_id, kind, snapshot): decided_by='auto', override=0. Refused (ApprovalError('not_auto_approvable'),
        nothing written) unless the quality verdict is exactly `pass` with no issues and the request neither requires an
        override nor carries issues; facts/Han/medical issues can therefore never be auto-approved. A row that already
        exists for the snapshot is honoured only if it is a plain pending/approved one (never an override request,
        never rejected/expired/superseded). The caller (the engine) decides when the config allows calling this."""
        if kind not in KINDS:
            raise ApprovalError('invalid_kind')
        if not isinstance(run_id, str) or not run_id or not isinstance(snapshot, str) or not snapshot:
            raise ApprovalError('invalid_request')
        if requires_override or _issue_list(issues):
            raise ApprovalError('not_auto_approvable')
        if not isinstance(quality, dict) or quality.get('verdict') != 'pass' or quality.get('issues'):
            raise ApprovalError('not_auto_approvable')
        with self.state.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            same = db.execute('SELECT * FROM approval_requests WHERE run_id=? AND kind=? AND snapshot=?',
                              (run_id, kind, snapshot)).fetchone()
            if same:
                if same['requires_override'] or _issue_list(json.loads(same['issues'] or '[]')):
                    raise ApprovalError('not_auto_approvable')
                if same['status'] == 'approved':
                    return same['id']
                if same['status'] != 'pending':
                    raise ApprovalError('not_auto_approvable')
                db.execute("UPDATE approval_requests SET status='approved', reason=?, decided_at=?, decided_by=?, "
                           "override=0 WHERE id=?", (AUTO_REASON, now, AUTO_BY, same['id']))
                return same['id']
            db.execute("UPDATE approval_requests SET status='superseded' WHERE run_id=? AND kind=? AND status='pending'",
                       (run_id, kind))
            return db.execute('INSERT INTO approval_requests(run_id,kind,snapshot,status,reason,created_at,deadline,'
                              "decided_at,decided_by,requires_override,issues,override) "
                              "VALUES(?,?,?,'approved',?,?,?,?,?,0,'[]',0)",
                              (run_id, kind, snapshot, AUTO_REASON, now, deadline, now, AUTO_BY)).lastrowid

    def is_approved(self, run_id, kind, snapshot):
        """True only for an `approved` row of exactly this (run_id, kind, snapshot)."""
        if not (isinstance(run_id, str) and isinstance(snapshot, str) and run_id and snapshot):
            return False
        with self.state.connection() as db:
            return db.execute("SELECT 1 FROM approval_requests WHERE run_id=? AND kind=? AND snapshot=? "
                              "AND status='approved'", (run_id, kind, snapshot)).fetchone() is not None

    def get(self, request_id):
        with self.state.connection() as db:
            row = db.execute('SELECT * FROM approval_requests WHERE id=?', (request_id,)).fetchone()
            return dict(row) if row else None

    def pending(self, kind=None):
        with self.state.connection() as db:
            if kind is None:
                rows = db.execute("SELECT * FROM approval_requests WHERE status='pending' ORDER BY id").fetchall()
            else:
                rows = db.execute("SELECT * FROM approval_requests WHERE status='pending' AND kind=? ORDER BY id",
                                  (kind,)).fetchall()
            return [dict(row) for row in rows]

    def record_messages(self, request_id, message_ids):
        ids = [int(i) for i in message_ids]
        with self.state.connection() as db:
            db.execute('UPDATE approval_requests SET message_ids=? WHERE id=?', (json.dumps(ids), request_id))


def _jpeg(path):
    from PIL import Image
    with Image.open(path) as source:
        picture = source.convert('RGB')
    out = io.BytesIO()
    picture.save(out, 'JPEG', quality=88)
    return out.getvalue()


def _when(value):
    try:
        return datetime.fromtimestamp(float(value), VN_TZ).strftime('%Y-%m-%d %H:%M (GMT+7)')
    except (TypeError, ValueError, OverflowError, OSError):
        return str(value) if isinstance(value, str) and value.strip() else DASH


def _text(value, limit=400):
    if not (isinstance(value, str) and value.strip()):
        return DASH
    value = ' '.join(value.split())
    return value if len(value) <= limit else value[:limit - 1] + '…'


def _head(value):
    return value[:12] if isinstance(value, str) and value else DASH


def _forecast(value):
    if not isinstance(value, dict):
        return DASH
    for key in ('usd', 'forecast_usd', 'estimated_usd', 'run_usd'):
        number = value.get(key)
        if isinstance(number, (int, float)) and not isinstance(number, bool):
            return f'{number:.2f} USD'
    spent = value.get('run_charged_or_estimated')
    if isinstance(spent, (int, float)) and not isinstance(spent, bool) and value.get('unit'):
        return f"{spent:g} {value['unit']} (chưa quy USD)"
    return DASH


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _count(value):
    return str(value) if isinstance(value, int) and not isinstance(value, bool) else DASH


def _commands(request_id, held=False):
    ident = request_id if request_id is not None else '<id>'
    text = f'/nailblog ok {ident}\n/nailblog reject {ident} <lý do>'
    if held:
        text += f'\nBài trượt kiểm chất lượng — cần /nailblog ok {ident} override <lý do>'
    return text


def build_preview(kind, artifact, image_paths, *, approval_id=None, deadline=None, environment='',
                  requires_override=None, issues=None):
    """Return (jpeg_bytes_list, text_card, preview_html). Sources on disk are only read, never rewritten.

    approval_id/deadline may also come from artifact['approval_id'] / artifact['approval_deadline'];
    without an id the card shows the literal `<id>` placeholder.
    """
    if kind not in KINDS:
        raise ApprovalError('invalid_kind')
    artifact = artifact if isinstance(artifact, dict) else {}
    approval_id = artifact.get('approval_id') if approval_id is None else approval_id
    deadline = artifact.get('approval_deadline') if deadline is None else deadline
    held = bool(artifact.get('requires_override') if requires_override is None else requires_override)
    issue_rows = _issue_list(artifact.get('issues') if issues is None else issues)
    article = artifact.get('article') if isinstance(artifact.get('article'), dict) else {}
    if kind == 'images':
        jpegs = [_jpeg(path) for path in (image_paths or ())]
        quality = artifact.get('quality') if isinstance(artifact.get('quality'), dict) else {}
        facts = artifact.get('facts') if isinstance(artifact.get('facts'), dict) else {}
        ungrounded = facts.get('ungrounded')
        lines = ['NailBlog duyệt bài + ảnh', f"Tiêu đề: {_text(article.get('title'), 200)}",
                 f"Tóm tắt: {_text(article.get('summary'), 500)}", f"Số chữ: {_count(artifact.get('words'))}",
                 f"Gate chất lượng: {_text(quality.get('verdict'))}",
                 f"Dữ kiện chưa có nguồn: {_count(len(ungrounded)) if isinstance(ungrounded, list) else DASH}"]
        bypass = artifact.get('review_bypass')
        if bypass and (not isinstance(bypass, dict) or bypass.get('enabled', True)):
            lines.append('ảnh chưa qua model chấm (bypass)')
        spend = artifact.get('image_spend')
        if artifact.get('image_source', {}).get('route') == 'subscription':
            cost = 'Ảnh qua gói subscription; chi phí gói/quota chưa xác định, không phải hóa đơn API 0 USD'
        elif isinstance(spend, dict) and all(_is_number(spend.get(k)) for k in ('settled_usd', 'daily_left_usd')):
            cost = (f"Chi phí ảnh đã chi (quyết toán): {spend['settled_usd']:.2f} USD; "
                    f"còn lại hôm nay: {spend['daily_left_usd']:.2f} USD")
        else:
            cost = f"Dự báo chi phí ảnh: {_forecast(artifact.get('image_quota_forecast'))}"
        lines += [cost,
                  f"Slot: {_text(artifact.get('slot_at'))}", f'Hạn duyệt: {_when(deadline)}',
                  f"Snapshot: {_head(artifact.get('snapshot'))}", f'Ảnh đính kèm: {len(jpegs)}']
    else:
        jpegs = []
        admin_id = artifact.get('admin_id')
        lines = ['NailBlog duyệt Draft trên admin',
                 f"admin_id: {_text(str(admin_id)) if admin_id not in (None, '') else DASH}",
                 f"Fingerprint Draft: {_head(artifact.get('draft_fingerprint'))}", f'Hạn duyệt: {_when(deadline)}']
    if environment == 'staging':
        lines[0] = '[staging] ' + lines[0]
    if held or issue_rows:
        lines.append(f'Lỗi kiểm chất lượng ({len(issue_rows)}):')
        lines += [f"- {row['code']}: {row['message']}" if row['message'] else f"- {row['code']}"
                  for row in issue_rows[:10]]
    tail = '\n\n' + _commands(approval_id, held)
    body = '\n'.join(lines)
    if len(body) + len(tail) > CARD_LIMIT:
        body = body[:CARD_LIMIT - len(tail) - 1] + '…'
    card = body + tail
    figures = ''.join('<figure><img alt="ảnh {0}" src="data:image/jpeg;base64,{1}"></figure>'.format(
        index, base64.b64encode(data).decode('ascii')) for index, data in enumerate(jpegs, 1))
    page = ('<!doctype html><html lang="vi"><meta charset="utf-8"><title>NailBlog preview</title><body>'
            '<pre>' + html.escape(card) + '</pre>' + figures + '</body></html>')
    return jpegs, card, page
