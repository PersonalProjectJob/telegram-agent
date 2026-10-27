"""Autoflow engine: topic locked -> text -> images -> Owner ok -> Draft -> Owner ok -> scheduled.

One resumable step per run per `run_due` call. Every stage lives in the `autoflow_runs` table, so a restart
continues where it stopped; the OnePost artifact phase is consulted before each side effect, so a step that already
happened is never repeated. This module NEVER publishes: no publish_pass, no OnePost.publish, no admin publish.
The scheduler publishes; the engine only reports (`mark_published`) once the post is `published`.

Handoff notes (S9):
- `service.media_send` / `service.notice_send` are used when bound; with no media sender a card is simply not sent.
- `service.browser_lock` is never touched here: `save_remote_draft` takes it itself (not reentrant).
- Rows keep only sanitized codes (`terminal_reason`), never remote text, article text or credentials.
"""
import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

from .approvals import ApprovalError, build_preview
from .images.pipeline import DispatchPaused
from .notices import notice_key, send_once
from .schedule import VN_TZ, approve_deadline, image_deadline, slot_datetime
from .state import InvalidTransition

LOG = logging.getLogger(__name__)

STAGES = ('new', 'images_send', 'images_wait', 'images_retry', 'saving', 'draft_send', 'draft_wait', 'scheduled',
          'published', 'held', 'skipped')
TERMINAL = ('published', 'held', 'skipped')
DRAFT_BLACKOUT_SECONDS = 20 * 60   # no Draft is saved within this long before ANY configured slot
MAX_FAILURES = 3
BACKOFF_SECONDS = 300        # 5 min, doubling per consecutive failure
MAX_TERMINAL_NOTICES = 50

# Outcomes that need a human to reconcile or can never succeed by repeating: held, one DM, never retried.
HOLD_CODES = frozenset({
    'text_requires_manual_reconciliation', 'candidate_requires_reconciliation', 'quality_pass_required',
    'onepost_held', 'create_requires_manual_reconciliation',
    'approved_topic_required', 'topic_already_claimed', 'topic_already_has_post', 'onepost_binding_changed',
    'legacy_onepost_requires_reconciliation', 'article_artifacts_require_reconciliation',
    'article_artifact_write_failed', 'unexpected_han_requires_reconciliation', 'unexpected_han_requires_review',
    'image_approval_snapshot_changed', 'draft_approval_snapshot_changed', 'image_approval_changed',
    'image_provenance_changed', 'complete_reviewed_image_provenance_required', 'manual_image_acceptance_required',
    'create_outcome_uncertain', 'test_image_candidate_requires_reconciliation', 'approval_deadline_passed',
    'text_quality_held', 'text_unknown', 'text_held_by_worker', 'duplicate_on_site', 'unknown_onepost_run',
})
HOLD_SUFFIXES = ('_requires_reconciliation', '_requires_manual_reconciliation', '_require_reconciliation')


def is_hold_code(code):
    """Structural, not a hand list: any reconciliation code (known or added later) is held and never retried,
    because repeating a step after an unknown outcome could dispatch a second paid call."""
    return code in HOLD_CODES or code.endswith(HOLD_SUFFIXES)
# paused/mode changed mid-pass, or before_dispatch said stop (run is resumable): retry next tick, no failure, no DM
QUIET_CODES = frozenset({'explicit_live_unpaused_required', 'dispatch_stopped'})
# An unknown Create is reconciled read-only by the service (it hands over to a human after 30 minutes): not a failure,
# just wait. Repeating the step is safe because the service never POSTs again once the Create claim exists.
RECONCILING_CODE = 'create_unknown_reconciling'
RECONCILE_RETRY_SECONDS = 300

REASON_TEXT = {
    'owner_images_rejected': 'ảnh bị từ chối (đã thử lại tối đa một lần), bài chuyển sang giữ lại',
    'owner_draft_rejected': 'Draft bị từ chối, bài chuyển sang giữ lại, Draft vẫn chưa đăng trên admin',
    'images_deadline_missed': 'hết hạn duyệt ảnh, bỏ slot này',
    'draft_deadline_missed': 'hết hạn duyệt Draft, bỏ slot này (Draft chưa đăng)',
    'approval_deadline_missed': 'hết hạn duyệt, bỏ slot này',
    'post_held': 'bài đang bị giữ lại, autoflow dừng',
    'category_unmapped': 'danh mục chưa có GUID trong cấu hình admin.category_ids',
    'approval_superseded': 'yêu cầu duyệt bị thay thế, cần kiểm tra thủ công',
    'unexpected_phase': 'pipeline ở trạng thái không mong đợi, cần kiểm tra thủ công',
    'text_quality_held': 'bài viết không qua cổng chất lượng và có lỗi không được override (facts/Hán/y tế/cấu trúc), '
                         'không tạo ảnh',
    'text_unknown': 'Agy không trả kết quả đọc được cho job viết bài (đã thử 1 job mới), cần kiểm tra thủ công',
    'text_held_by_worker': 'worker Agy từ chối job viết bài, cần kiểm tra thủ công',
    'duplicate_on_site': 'đã có bài trùng tiêu đề/slug trên trang (mọi trạng thái), không tạo Draft',
    'unknown_onepost_run': 'run không có bản ghi one-post tương ứng, không đăng',
    'approval_deadline_passed': 'Owner duyệt Draft đúng hạn nhưng xử lý sau hạn, không tự duyệt (Draft chưa đăng)',
    'images_not_approved': 'ảnh đã được chấp nhận ngoài luồng autoflow, chưa có duyệt của Owner, không tự lưu Draft',
    'topic_changed': 'chủ đề không còn được duyệt hoặc không còn giữ slot này, dừng trước bước trả phí/lưu/duyệt tiếp theo',
    'post_changed_outside_autoflow': 'bài bị đổi trạng thái ngoài autoflow (không qua /nailblog ok), autoflow dừng',
    'publish_not_done': 'quá giờ slot hơn 1 tiếng mà chưa xác nhận đã đăng (bài có thể đã lên trang dù hệ thống ghi '
                        'đã lên lịch); không đăng lại, kiểm tra trang quản trị',
    'image_retry_interrupted': 'lần tạo lại ảnh duy nhất bị gián đoạn (tạm dừng hoặc hết ngân sách), chưa có ảnh mới',
}
PUBLISH_GRACE_SECONDS = 3600   # a post still `scheduled` this long after its slot was not published


def run_id_for(slot_at, topic_id):
    """`auto-YYYYMMDD-HHMM-t<topic>` in Vietnam time."""
    slot = datetime.fromisoformat(slot_at).astimezone(VN_TZ)
    return f"auto-{slot:%Y%m%d-%H%M}-t{int(topic_id)}"


def in_slot_blackout(config, now):
    """True within 20 minutes before any configured slot (today's or tomorrow's)."""
    moment = datetime.fromtimestamp(now, VN_TZ)
    for offset in (0, 1):
        day = (moment + timedelta(days=offset)).date()
        for hhmm in config.slots:
            slot = slot_datetime(day, hhmm).timestamp()
            if slot - DRAFT_BLACKOUT_SECONDS <= now < slot:
                return True
    return False


class AutoflowRuns:
    """The `autoflow_runs` table (owned here). `ctx` is extra JSON the engine needs to resume."""

    def __init__(self, state):
        self.state = state
        with state.connection() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS autoflow_runs(
                run_id TEXT PRIMARY KEY, topic_id INTEGER NOT NULL UNIQUE, slot_at TEXT NOT NULL,
                out_dir TEXT NOT NULL, run_usd REAL NOT NULL DEFAULT 0, stage TEXT NOT NULL,
                failures INTEGER NOT NULL DEFAULT 0, next_attempt_at REAL NOT NULL DEFAULT 0,
                terminal_reason TEXT NOT NULL DEFAULT '', ctx TEXT NOT NULL DEFAULT '{}')''')

    def get(self, run_id):
        with self.state.connection() as db:
            row = db.execute('SELECT * FROM autoflow_runs WHERE run_id=?', (run_id,)).fetchone()
            return dict(row) if row else None

    def by_topic(self, topic_id):
        with self.state.connection() as db:
            row = db.execute('SELECT * FROM autoflow_runs WHERE topic_id=?', (topic_id,)).fetchone()
            return dict(row) if row else None

    def rows(self, *, active=None):
        sql = 'SELECT * FROM autoflow_runs'
        marks = ','.join('?' * len(TERMINAL))
        if active is True:
            sql += f' WHERE stage NOT IN ({marks})'
        elif active is False:
            sql += f' WHERE stage IN ({marks})'
        with self.state.connection() as db:
            return [dict(row) for row in db.execute(sql + ' ORDER BY slot_at, run_id',
                                                    TERMINAL if active is not None else ())]

    def create(self, run_id, topic_id, slot_at, out_dir):
        # `run_usd` is a dead legacy column (API-era run budget): kept because existing staging DBs declare it NOT NULL.
        with self.state.connection() as db:
            return db.execute('INSERT OR IGNORE INTO autoflow_runs(run_id,topic_id,slot_at,out_dir,run_usd,stage) '
                              "VALUES(?,?,?,?,0,'new')", (run_id, topic_id, slot_at, out_dir)).rowcount == 1

    def update(self, run_id, **fields):
        ctx = fields.pop('ctx', None)
        with self.state.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if ctx is not None:
                current = json.loads(db.execute('SELECT ctx FROM autoflow_runs WHERE run_id=?', (run_id,)).fetchone()[0])
                current.update(ctx)
                fields['ctx'] = json.dumps({k: v for k, v in current.items() if v is not None}, sort_keys=True)
            if fields:
                names = ','.join(f'{name}=?' for name in fields)
                db.execute(f'UPDATE autoflow_runs SET {names} WHERE run_id=?', (*fields.values(), run_id))
        return self.get(run_id)


def _code(error):
    text = str(error)
    return text if re.fullmatch(r'[a-z][a-z0-9_]{2,80}', text) else type(error).__name__


_NO_BINDING = object()


def _ctx(row):
    return json.loads(row['ctx'] or '{}')


class _Env:
    def __init__(self, service, stop, onepost, approvals, now):
        self.service, self.stop, self.onepost, self.approvals, self.now = service, stop, onepost, approvals, now
        self.state, self.config = service.state, service.config
        self.runs = AutoflowRuns(self.state)
        self.owner = self.config.owner_ids[0] if len(self.config.owner_ids) == 1 else None
        self.before_dispatch = (lambda: True) if stop is None else (lambda: not stop.is_set())
        self.tag = '[staging] ' if self.config.contract.environment == 'staging' else ''

    async def dm(self, key, text):
        send = self.service.notice_send
        if send is None:
            return False
        return await send_once(self.state, send, key, self.tag + text, now=self.now)

    def post_id(self, row):
        try:
            return self.onepost.artifact(row['run_id']).get('post_id')
        except Exception:   # no artifact yet -> no post
            return None

    def owns_slot(self, row):
        """The topic is still approved, still on the run's slot, and no other approved topic holds that slot."""
        topic = self.state.get_topic(row['topic_id'])
        return bool(topic and topic['state'] == 'approved' and topic['slot_at'] == row['slot_at']
                    and self.state.slot_holder(row['slot_at'], excluding=row['topic_id']) is None)

    def move(self, row, stage, ctx=None):
        return self.runs.update(row['run_id'], stage=stage, failures=0, next_attempt_at=0, **({'ctx': ctx} if ctx else {}))

    def image_ts(self, row):
        return image_deadline(datetime.fromisoformat(row['slot_at']), self.config.schedule.window_end_hours).timestamp()

    def approve_ts(self, row):
        return approve_deadline(datetime.fromisoformat(row['slot_at']), self.config.schedule.approve_deadline_hours).timestamp()

    def refresh(self):
        """Fresh clock per row: a slow earlier row must not leave later rows judged against the pass start."""
        clock = getattr(self.service, 'clock', None)
        if callable(clock):
            self.now = clock()

    def halted(self):
        return (self.stop is not None and self.stop.is_set()) or self.state.control()['paused'] \
            or self.config.mode != 'live'

    def hold_post(self, post_id, reason):
        if not post_id:
            return
        try:
            self.state.transition(post_id, 'held', reason)
        except InvalidTransition:
            LOG.info('NailBlog autoflow post not held post=%s reason=%s', post_id, reason)

    async def finish(self, row, stage, reason):
        updated = self.runs.update(row['run_id'], stage=stage, terminal_reason=reason, failures=0, next_attempt_at=0)
        LOG.info('NailBlog autoflow run=%s stage=%s reason=%s', row['run_id'], stage, reason)
        notice = _terminal_notice(updated)
        if notice:
            await self.dm(*notice)

    async def transient(self, row, code, ctx=None):
        failures = row['failures'] + 1
        LOG.warning('NailBlog autoflow run=%s stage=%s failure=%s code=%s', row['run_id'], row['stage'], failures, code)
        if failures >= MAX_FAILURES:
            await self.finish(row, 'held', 'too_many_failures:' + code)
            return
        self.runs.update(row['run_id'], failures=failures, next_attempt_at=self.now + BACKOFF_SECONDS * 2 ** (failures - 1),
                         **({'ctx': ctx} if ctx else {}))

    def settle(self, request_id):
        """The request row, marking a pending request past its deadline as expired through the store's own API."""
        found = self.approvals.get(request_id)
        if found and found['status'] == 'pending' and self.now > found['deadline'] and self.owner:
            self.approvals.decide(request_id, 'reject', 'deadline_expired', self.owner, self.owner, self.now)
            found = self.approvals.get(request_id)
        return found

    def preview_artifact(self, art, row):
        """Artifact as build_preview wants it: slot added; images come from the subscription, no API cost."""
        out = {**art, 'slot_at': row['slot_at'],
               'image_source': {'route': 'subscription', 'api_usd': None, 'plan_cost_included': 'unknown'}}
        if not out.get('review_bypass') and getattr(self.config.image, 'review_bypass', False):
            out['review_bypass'] = {'enabled': True, 'source': 'config'}
        return out

    def image_paths(self, run_id):
        document = json.loads(self.onepost.read(run_id)['document'])
        return [document['cover_path'], *document['image_paths']]

    async def send_card(self, row, request_id, files, card):
        """True when the card went out and its message ids are recorded."""
        send = self.service.media_send
        if send is None or self.owner is None:
            LOG.warning('NailBlog autoflow run=%s cannot send approval card: no media sender or Owner', row['run_id'])
            return False
        try:
            ids = await send(self.owner, files, card)
        except Exception as error:
            sent = getattr(error, 'sent_ids', None)
            if sent:
                self.approvals.record_messages(request_id, sent)
            await self.transient(row, 'media_send_failed', ctx={'resend': request_id})
            return False
        self.approvals.record_messages(request_id, ids or [])
        return True


def _terminal_notice(row):
    if not row or row['stage'] not in ('held', 'skipped'):
        return None
    reason = row['terminal_reason'] or 'unknown'
    text = REASON_TEXT.get(reason.split(':')[0], 'cần kiểm tra thủ công')
    code = reason.split(':', 1)[1] if reason.startswith('too_many_failures:') else None
    if code:
        text = f'lỗi lặp lại {MAX_FAILURES} lần ({code}), bài chuyển sang giữ lại'
    elif is_hold_code(reason):
        text = f'cần đối soát thủ công ({reason}), không tự thử lại'
    verb = 'bỏ qua' if row['stage'] == 'skipped' else 'giữ lại'
    return (notice_key('autoflow', f"{row['run_id']}|{row['stage']}"),
            f"NailBlog autoflow: bài #{row['topic_id']} (slot {row['slot_at']}, run {row['run_id']}) {verb}: {text}.")


# ---- stage handlers ------------------------------------------------------------------------------------------

ROUTE = {'images_ready': 'images_send', 'images_approved': 'saving', 'draft_verified': 'draft_send',
         'scheduled': 'scheduled', 'published': 'scheduled'}


def _override(art):
    """Extra `ApprovalStore.request` arguments for a run the gate held with only overridable issues: plain `ok` is then
    refused and only `/nailblog ok <id> override <reason>` approves. Nothing extra for a passing run."""
    if not art.get('requires_override'):
        return {}
    return {'requires_override': True, 'issues': art.get('issues') or []}


def _override_args(env, ctx, art):
    """Only a run the gate held (fixable issues) hands the store what it needs to honour the Owner's override."""
    if not art.get('requires_override'):
        return {}
    return {'approvals': env.approvals, 'images_snapshot': (ctx.get('images') or {}).get('snapshot')}


async def _route(env, row, art):
    stage = ROUTE.get(art.get('phase'))
    if stage is None:
        await env.finish(row, 'held', 'onepost_held' if art.get('phase') == 'held' else 'unexpected_phase')
    else:
        env.move(row, stage)


async def _prepare(env, row):
    art = await env.onepost.prepare(row['run_id'], row['topic_id'], row['out_dir'], before_dispatch=env.before_dispatch)
    await _route(env, row, art)


async def _new(env, row):
    await _prepare(env, row)


async def _images_send(env, row):
    art = env.onepost.artifact(row['run_id'])
    if art['phase'] != 'images_ready':
        return await _route(env, row, art)
    deadline = env.image_ts(row)
    try:
        request_id = env.approvals.request('images', row['run_id'], art['snapshot'], deadline, env.now,
                                           **_override(art))
    except ApprovalError as error:
        if str(error) == 'snapshot_superseded':
            return await env.finish(row, 'held', 'approval_superseded')
        if str(error) == 'not_overridable':        # the store refuses a card whose issues the Owner may not override
            env.hold_post(art.get('post_id'), 'text_quality_held')
            return await env.finish(row, 'held', 'text_quality_held')
        raise
    request = env.approvals.get(request_id)
    unsent = not json.loads(request['message_ids'] or '[]') or _ctx(row).get('resend') == request_id
    if request['status'] == 'pending' and unsent:
        files, card, _page = build_preview('images', env.preview_artifact(art, row), env.image_paths(row['run_id']),
                                           approval_id=request_id, deadline=deadline,
                                           environment=env.config.contract.environment)
        if not await env.send_card(row, request_id, files, card):
            return
    env.move(row, 'images_wait', ctx={'images': {'id': request_id, 'snapshot': art['snapshot']}, 'resend': None})


async def _images_wait(env, row):
    ctx = _ctx(row)
    found = env.settle(ctx['images']['id'])
    snapshot = ctx['images']['snapshot']
    status = found['status']
    if status == 'expired':
        return await env.finish(row, 'skipped', 'images_deadline_missed')
    if status == 'superseded':
        return env.move(row, 'images_send')
    if status == 'rejected':
        if ctx.get('retried', 0) >= 1:
            return await _images_rejected_for_good(env, row)
        if env.now >= env.image_ts(row):          # no paid retry once the image deadline has passed
            return await env.finish(row, 'skipped', 'images_deadline_missed')
        row = env.move(row, 'images_retry', ctx={'retried': 1, 'rejected_snapshot': snapshot,
                                                 'retry_reason': found['reason'] or 'owner_rejected'})
        return await _images_retry(env, row)
    if status == 'approved' and env.approvals.is_approved(row['run_id'], 'images', snapshot):
        art = env.onepost.artifact(row['run_id'])
        if art['phase'] == 'images_ready':
            env.onepost.accept_images(row['run_id'], snapshot)
        env.move(row, 'saving')


async def _images_rejected_for_good(env, row, reason='owner_images_rejected'):
    env.hold_post(env.post_id(row), reason)   # no artifact -> there is no post to hold
    await env.finish(row, 'held', reason)


async def _images_retry(env, row):
    ctx = _ctx(row)
    art = env.onepost.artifact(row['run_id'])
    if art['phase'] == 'images_ready' and art['snapshot'] != ctx.get('rejected_snapshot'):
        return env.move(row, 'images_send')        # the retry already completed before a restart
    if art['phase'] != 'images_ready':
        # retry_images stores `images_pending` BEFORE it reruns the pipeline: a run found there crashed or failed
        # mid-retry. The outcome is unknown and the single retry is spent, so hold instead of spending again. The
        # Owner did not reject anything here: the reason says the retry was interrupted.
        return await _images_rejected_for_good(env, row, 'image_retry_interrupted')
    if env.halted():
        return                                      # paused/stopped: nothing is spent, try again next tick
    try:
        await env.onepost.retry_images(row['run_id'], ctx.get('retry_reason') or 'owner_rejected')
    except Exception as error:
        LOG.warning('NailBlog autoflow run=%s retry_images failed code=%s', row['run_id'], _code(error))
        # OnePost.retry_images runs active() BEFORE it records the spent retry: a pause/stop refusal that left the run
        # at images_ready consumed nothing and is transient. Any other failure (or a phase that moved on) spent it.
        if _code(error) in QUIET_CODES and env.onepost.artifact(row['run_id'])['phase'] == 'images_ready':
            return
        # The retry is spent. Pause/stop, the daily cap, a pre-dispatch failure or any other error: the Owner did not
        # reject anything, so never report `owner_images_rejected` here.
        return await _images_rejected_for_good(env, row, 'image_retry_interrupted')
    env.move(row, 'images_send')


async def _saving(env, row):
    art = env.onepost.artifact(row['run_id'])
    if art['phase'] != 'images_approved':
        return await _route(env, row, art)
    if env.now >= env.approve_ts(row):
        return await env.finish(row, 'skipped', 'draft_deadline_missed')
    if in_slot_blackout(env.config, env.now):
        return                                      # wait; never a Draft within 20 minutes before a slot
    if not env.approvals.is_approved(row['run_id'], 'images', (_ctx(row).get('images') or {}).get('snapshot')):
        return await env.finish(row, 'held', 'images_not_approved')   # e.g. an operator CLI accept on this run
    topic = env.state.get_topic(row['topic_id']) or {}
    category_id = env.config.admin.category_ids.get(topic.get('category'))
    if not category_id:
        return await env.finish(row, 'held', 'category_unmapped')
    await env.onepost.save(row['run_id'], category_id)   # save_remote_draft takes browser_lock itself
    env.move(row, 'draft_send')


async def _left_review(env, row, art, *, owner_approved=False):
    """True (run held) when the Draft was approved/scheduled/published outside the engine: the run phase moved to
    `scheduled`/`published` (one-post CLI approve) or the post left `review`, with no Owner ok on the stored snapshot.
    A post still `scheduled` is put on `held` so it cannot publish. The one exception is a post the engine itself
    scheduled after the Owner's ok (crash between approve and the stage move)."""
    phase = art['phase']
    post = env.state.get_post(art['post_id']) if art.get('post_id') else None
    moved = phase in ('scheduled', 'published') or (phase == 'draft_verified' and post is not None
                                                    and post['state'] != 'review')
    if not moved or (owner_approved and post is not None and post['state'] in ('scheduled', 'published')):
        return False
    env.hold_post(art.get('post_id'), 'post_changed_outside_autoflow')
    await env.finish(row, 'held', 'post_changed_outside_autoflow')
    return True


async def _draft_send(env, row):
    art = env.onepost.artifact(row['run_id'])
    if await _left_review(env, row, art):
        return
    if art['phase'] != 'draft_verified':
        return await _route(env, row, art)
    if env.now >= env.approve_ts(row):
        return await env.finish(row, 'skipped', 'draft_deadline_missed')
    deadline = env.approve_ts(row)
    try:
        request_id = env.approvals.request('draft', row['run_id'], art['snapshot'], deadline, env.now,
                                           **_override(art))
    except ApprovalError as error:
        if str(error) == 'snapshot_superseded':
            return await env.finish(row, 'held', 'approval_superseded')
        if str(error) == 'not_overridable':        # the store refuses a card whose issues the Owner may not override
            env.hold_post(art.get('post_id'), 'text_quality_held')
            return await env.finish(row, 'held', 'text_quality_held')
        raise
    request = env.approvals.get(request_id)
    unsent = not json.loads(request['message_ids'] or '[]') or _ctx(row).get('resend') == request_id
    if request['status'] == 'pending' and unsent:
        files, card, _page = build_preview('draft', env.preview_artifact(art, row), [],
                                           approval_id=request_id, deadline=deadline,
                                           environment=env.config.contract.environment)
        if not await env.send_card(row, request_id, files, card):
            return
    env.move(row, 'draft_wait', ctx={'draft': {'id': request_id, 'snapshot': art['snapshot']}, 'resend': None})


async def _draft_wait(env, row):
    ctx = _ctx(row)
    found = env.settle(ctx['draft']['id'])
    snapshot = ctx['draft']['snapshot']
    status = found['status']
    owner_ok = status == 'approved' and env.approvals.is_approved(row['run_id'], 'draft', snapshot)
    if await _left_review(env, row, env.onepost.artifact(row['run_id']), owner_approved=owner_ok):
        return
    if status == 'expired':
        return await env.finish(row, 'skipped', 'draft_deadline_missed')
    if status == 'superseded':
        return env.move(row, 'draft_send')
    if status == 'rejected':
        env.hold_post(env.onepost.artifact(row['run_id']).get('post_id'), 'owner_draft_rejected')
        return await env.finish(row, 'held', 'owner_draft_rejected')
    if owner_ok:
        art = env.onepost.artifact(row['run_id'])
        post = env.state.get_post(art['post_id']) if art.get('post_id') else None
        if art['phase'] == 'draft_verified' and not (post and post['state'] == 'scheduled'):
            if env.now >= env.approve_ts(row):
                return await env.finish(row, 'held', 'approval_deadline_passed')
            env.onepost.approve(row['run_id'], snapshot, **_override_args(env, ctx, art))
        env.move(row, 'scheduled')


async def _scheduled(env, row):
    art = env.onepost.artifact(row['run_id'])
    post = env.state.get_post(art['post_id']) if art.get('post_id') else None
    if post is None:
        return
    if post['state'] == 'published':
        if art['phase'] != 'published':
            env.onepost.mark_published(row['run_id'])   # sync in OnePost
        await env.finish(row, 'published', '')
    elif post['state'] == 'held':
        await env.finish(row, 'held', 'post_held')
    elif post['state'] == 'scheduled' and env.now > datetime.fromisoformat(row['slot_at']).timestamp() + PUBLISH_GRACE_SECONDS:
        await env.finish(row, 'held', 'publish_not_done')   # the scheduler had its window; the post state is not touched
    elif post['state'] == 'uncertain':   # keep watching: it may still resolve to published (or held by the Owner)
        await env.dm(notice_key('publish_uncertain', row['run_id']),
                     f"NailBlog autoflow: bài #{row['topic_id']} (slot {row['slot_at']}) chưa xác định được kết quả đăng. "
                     'Kiểm tra trang quản trị, không đăng lại.')


HANDLERS = {'new': _new, 'images_send': _images_send, 'images_wait': _images_wait, 'images_retry': _images_retry,
            'saving': _saving, 'draft_send': _draft_send, 'draft_wait': _draft_wait, 'scheduled': _scheduled}
IMAGE_DEADLINE_STAGES = ('new', 'images_send', 'images_retry')
# Every stage before the post is scheduled re-checks that its topic is still approved and still owns the slot.
GUARDED_STAGES = ('new', 'images_send', 'images_wait', 'images_retry', 'saving', 'draft_send', 'draft_wait')


async def _advance(env, row):
    try:
        if row['stage'] in IMAGE_DEADLINE_STAGES and env.now >= env.image_ts(row):
            return await env.finish(row, 'skipped', 'images_deadline_missed')
        if row['next_attempt_at'] > env.now:
            return
        if row['stage'] in GUARDED_STAGES and not env.owns_slot(row):
            # Swapped/dropped topic: nothing paid, saved or approved from here on.
            env.hold_post(env.post_id(row), 'topic_changed')
            return await env.finish(row, 'held', 'topic_changed')
        await HANDLERS[row['stage']](env, row)
    except DispatchPaused:
        return                                      # stop/pause before any transport call: no failure, retry next pass
    except Exception as error:
        code = _code(error)
        if code in QUIET_CODES:
            return
        if code == RECONCILING_CODE:
            env.runs.update(row['run_id'], next_attempt_at=env.now + RECONCILE_RETRY_SECONDS)
            return
        if is_hold_code(code):
            return await env.finish(row, 'held', code)
        await env.transient(env.runs.get(row['run_id']), code)


def _new_runs(env):
    """Returns the approved topics whose window is open but that the daily decision did NOT lock (one DM each)."""
    # Create rows for topics LOCKED by the daily decision (see daily.locked_topic) whose slot is inside the start
    # window and not yet past its image deadline, at most one run per slot.
    from .daily import locked_topic
    unlocked = []
    config = env.config
    taken = set()          # one run per slot, ever: a swapped/dropped topic can never start a second paid run
    for existing in env.runs.rows():
        try:
            taken.add(datetime.fromisoformat(existing['slot_at']).timestamp())
        except ValueError:
            continue
    for topic in env.state.list_topics(('approved',)):
        if not topic['slot_at'] or env.runs.by_topic(topic['id']) or env.state.posts_for_topic(topic['id']):
            continue
        try:
            slot = datetime.fromisoformat(topic['slot_at'])
            run_id = run_id_for(topic['slot_at'], topic['id'])
            if slot.tzinfo is None:
                continue
        except ValueError:
            continue
        if slot.timestamp() in taken:
            continue
        start = slot.timestamp() - config.schedule.window_start_hours * 3600
        if not start <= env.now < image_deadline(slot, config.schedule.window_end_hours).timestamp():
            continue
        if not locked_topic(env.state, topic):
            local = slot.astimezone(VN_TZ)
            if env.state.daily_get((local.date() - timedelta(days=1)).isoformat(), 'decide') is not None:
                unlocked.append((topic, local))     # decided, yet this topic was not chosen: say so, do not skip silently
            continue
        out_dir = str(Path(config.autoflow.runs_dir) / run_id)
        if env.runs.create(run_id, topic['id'], topic['slot_at'], out_dir):
            taken.add(slot.timestamp())
    return unlocked


async def run_due(service, stop, *, onepost, approvals, now):
    """One engine pass. No side effects unless live + unpaused + armed + `autoflow.enabled`."""
    config, state = service.config, service.state
    if isinstance(now, datetime):
        now = now.timestamp()
    if config.mode != 'live' or not config.autoflow.enabled or state.control()['paused']:
        return
    armed = state.armed_binding()
    loaded = getattr(service, 'startup_binding', _NO_BINDING)   # a service without one (test double): non-empty is enough
    if not armed or (loaded is not _NO_BINDING and armed != loaded):
        return                                       # armed for another build/config than this process loaded
    env = _Env(service, stop, onepost, approvals, now)
    if env.owner is None:
        return
    for topic, local in _new_runs(env):
        await env.dm(notice_key('unlocked', f"{topic['id']}|{topic['slot_at']}"),
                     f"NailBlog autoflow: slot {local:%H:%M} ngày {local:%d/%m} chưa có chủ đề được chốt: chủ đề "
                     f"#{topic['id']} đã duyệt nhưng bước chốt hằng ngày không chọn nó (ví dụ ngoài nhóm danh mục của "
                     'slot), nên autoflow không chạy slot này.')
    for row in env.runs.rows(active=True):
        if stop is not None and stop.is_set():
            break
        env.refresh()
        await _advance(env, env.runs.get(row['run_id']))
    env.refresh()
    for row in env.runs.rows(active=False)[-MAX_TERMINAL_NOTICES:]:   # a DM that failed is retried by the notice backoff
        notice = _terminal_notice(row)
        if notice:
            await env.dm(*notice)
