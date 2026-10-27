"""Service loop, preview pipelines and staging publication guarded by durable approval.

Step B adds `plan_week` (sources, planner, topics) and `preview` (write one article to files). Neither has any
website write path: sources are read with GET only and preview reads no website at all.
"""

import asyncio
import html
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from . import daily, facts
from ._runtime import AlreadyRunning, ProcessLock
from .commands import execute
from .model_lock import MODEL_LOCK
from .notices import notice_key, send_once, uncertain_notice
from .planner import (format_topics_dm, next_week_start, propose, slots_of_config, store_topics, topic_meta,
                      week_label)
from .quality import gate
from .schedule import VN_TZ, is_due_for_publish
from .sources import SourceError, fetch_feeds, fetch_posts
from .state import State, startup_binding
from .text import TextModel, TextModelError
from .usage import UsageRecorder, text_cost, write_atomic
from .writer import (annotate_repair_outcome, build_jsonld, editorial_repair_issues, rebalance_paragraphs, han_fields, hold_unexpected_han, placeholder_images, render_html,
                     repair_article, trim_list_items, word_count, write_article)

LOG = logging.getLogger(__name__)
TICK_SECONDS = 60
CACHE_NAME = "blog-cache.json"
OCCUPYING_STATES = ("writing", "review", "scheduled", "before_publish", "published", "uncertain")
WEEK_DAYS = 7   # a weekly plan needs WEEK_DAYS x (configured slots per day) topics
READBACK_BACKOFF_SECONDS = 300
READBACK_BACKOFF_MAX_SECONDS = 3600
REFUSAL_TEXT = {
    'daily_publish_limit': 'đã đủ số bài được đăng trong ngày (daily_publish_limit), bài này không được đăng.',
    'outside_publish_window': 'đã ngoài cửa sổ đăng (15 phút quanh slot), không đăng bù.',
}
# Preview only: an image that is not made yet shows as an empty dashed frame with its brief as the note.
PREVIEW_STYLE = ("<style>figure:has(img[src^=\"placeholder-\"]){border:2px dashed #999;padding:1em;min-height:12em;"
                 "margin:1em 0}img[src^=\"placeholder-\"]{display:none}figure:has(img[src^=\"placeholder-\"]) "
                 "figcaption{color:#666;font-style:italic}</style>")


class PreviewError(RuntimeError):
    """The requested topic cannot be previewed."""


@dataclass
class PlanOutcome:
    text: str
    topic_ids: list = field(default_factory=list)
    sent: bool = False
    created: bool = True


@dataclass
class PreviewOutcome:
    verdict: str
    words: int
    issues: list
    json_path: str
    html_path: str
    repaired: list = field(default_factory=list)


@dataclass
class Draft:
    """One written article with everything the gate and facts check said about it."""
    article: dict
    content: str
    images: list
    ungrounded: list
    result: object
    words: int
    repaired: list


async def wait_for_stop(stop, seconds):
    """Interruptible pacing; return whether shutdown was requested."""
    try:
        await asyncio.wait_for(stop.wait(), seconds)
    except TimeoutError:
        pass
    return stop.is_set()


class _Meter:
    """Token counts reported by ai_transport.complete through its `usage_sink` hook."""

    def __init__(self):
        self.calls = self.prompt = self.completion = 0
        self.uncertain = False

    def add(self, usage):
        if usage.get('_uncertain'):
            self.uncertain = True
            return
        self.calls += 1
        self.prompt += int(usage.get("prompt_tokens", 0))
        self.completion += int(usage.get("completion_tokens", 0))


class Service:
    def __init__(self, config, *, state=None, clock=time.time, config_path=None):
        self.config = config
        # The binding this process loaded, computed ONCE at start: `/nailblog resume` arms exactly this value and
        # refuses (restart_required) when the build/config on disk no longer matches it. None = could not be computed.
        try:
            self.startup_binding = startup_binding(config, config_path=config_path)[0]
        except Exception:
            self.startup_binding = None
        self.state = state or State(config.db_path, image_mode=config.schedule.image_mode)
        self.clock = clock
        self.usage = UsageRecorder(self.state, config, clock=clock)
        self.notice_send = None  # Bound only after the existing client verifies the Owner.
        self.notice_owner_id = None
        self._announced = set()
        self._bad_rows = set()
        self.opener = None       # injected by tests; None means the real network openers
        self.text = None         # injected by tests; None means a TextModel on this service's model_call
        self.job_failures = {}   # (day, step) -> (failures, retry not before)
        self.refresh_trends = True  # False: a CLI run for a past day (--date) must not fetch today's real trends
        self._jobs = None
        self.browser_lock = asyncio.Lock()  # one browser user at a time: publish_pass and save_remote_draft
        self.media_send = None   # bound later (Owner media sender); only the attribute lives here
        self.readback_backoff = {}  # post id -> (consecutive timeouts, retry not before) for uncertain readback

    def command(self, name, args=()):
        return execute(self.state, name, args, now=self.clock(), config=self.config)

    def due_posts(self, now=None):
        now = self.clock() if now is None else now
        moment = datetime.fromtimestamp(now, VN_TZ)
        due = []
        for row in self.state.list_posts(("scheduled",)):
            try:
                if is_due_for_publish(moment, datetime.fromisoformat(row["slot_at"]), self.config.late_window_minutes):
                    due.append(row)
            except (TypeError, ValueError):
                # One corrupt row must never block the others; say so once per process.
                if row["id"] not in self._bad_rows:
                    self._bad_rows.add(row["id"])
                    LOG.warning("NailBlog skipped post %s: slot_at is not a timezone-aware ISO datetime", row["id"])
        return due

    def tick(self, now=None):
        """One scheduler pass. Step A never publishes: due posts are only logged and keep their state."""
        if self.state.control()["paused"]:
            return []
        due = self.due_posts(now)
        for row in due:
            if row["id"] not in self._announced:
                self._announced.add(row["id"])
                if self.config.mode == "preview":
                    LOG.info("preview: would publish %s", row["id"])
                else:
                    LOG.info("live: post %s awaits guarded publish pass", row["id"])
        self.usage.publish()
        return [row["id"] for row in due]

    async def model_call(self, function, *args, stop=None, provider=None, model=None, before_dispatch=None):
        """Every model call yields to triage through the shared lock.

        With `provider`/`model` the call is accounted: a paid provider (anything but ollama) is refused when the
        daily budget is spent or the model has no price, BEFORE the request; usage is recorded AFTER it, even when
        the call failed. `function` then receives a keyword `usage_sink` (as ai_transport.complete does)."""
        paid = provider is not None and provider != "ollama"
        meter = _Meter() if provider is not None else None
        from .images.pipeline import DispatchPaused
        def check_dispatch():
            if before_dispatch is not None:
                try:
                    before_dispatch()
                except Exception:
                    raise DispatchPaused('workflow_paused_before_dispatch') from None
        check_dispatch()
        cancelled_before_dispatch = False
        if paid:
            self.usage.check()
            text_cost(self.config.budget, provider, model, 0, 0)  # unpriced model: refuse before spending
            from .paid import PaidLedger
            from uuid import uuid4
            ledger = PaidLedger(self.state, self.config, self.clock)
            intent = 'text-' + uuid4().hex
            # Transport limits input to 128 KiB. Gemini can make two attempts,
            # each with at most 32768 output/thinking tokens.
            attempts = 2 if provider == 'gemini' else 1
            bound = text_cost(self.config.budget, provider, model, attempts * 128 * 1024, attempts * 32768)
            ledger.reserve(intent, provider, model, bound)

        def run():
            with MODEL_LOCK.enter(background=True, stop=stop):
                check_dispatch()
                return function(*args, usage_sink=meter.add) if meter is not None else function(*args)
        # Cancelling to_thread does not stop the call; finish it before releasing resources.
        task = asyncio.create_task(asyncio.to_thread(run))
        try:
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise
        except DispatchPaused as error:
            if paid and not error.dispatched:
                from .paid import cancel_pending_before_dispatch
                cancel_pending_before_dispatch(ledger.state,'paid_intents',intent,self.clock)
                cancelled_before_dispatch = True
            raise
        finally:
            if meter is not None and (paid or meter.calls):
                usd = text_cost(self.config.budget, provider, model, meter.prompt, meter.completion)
                if paid and cancelled_before_dispatch:
                    self.usage.publish()
                elif paid:
                    if meter.calls and not meter.uncertain and meter.prompt + meter.completion > 0:
                        ledger.settle(intent, meter.prompt + meter.completion, usd)
                    else:
                        ledger.uncertain(intent)
                    self.usage.publish()
                else:
                    self.usage.record(provider, model, meter.prompt + meter.completion, usd)

    async def notify(self):
        if self.notice_send is None:
            return
        for row in self.state.list_posts(("uncertain",)):
            notice = uncertain_notice(row)
            if notice:
                await send_once(self.state, self.notice_send, *notice, now=self.clock())

    async def save_remote_draft(self, post_id, payload, *, cover_path, image_paths):
        async with self.browser_lock:
            # A hung draft save must not hold the browser forever; the error surfaces as any other failure.
            async with asyncio.timeout(self.config.admin.publish_timeout_seconds):
                return await self._save_remote_draft(post_id, payload, cover_path=cover_path, image_paths=image_paths)

    async def _save_remote_draft(self, post_id, payload, *, cover_path, image_paths):
        from .admin import Admin
        from .admin_state import AdminStore
        from .publication import AdminError
        from .admin_images import validate_files, verify_reviews
        if self.config.mode != 'live':
            raise AdminError('preview_write_disabled')
        browser = Admin(self.config)
        browser.write_ready()
        store = AdminStore(self.state, self.config)
        op = store.operation(post_id)
        if op is None:
            row = self.state.get_post(post_id)
            if not row:
                raise AdminError('draft_state_invalid')
            if han_fields(payload) or han_fields(json.loads(row['draft_json'])):
                raise AdminError('unexpected_han_requires_review')
            store.check_write(post_id)
            hashes = validate_files(cover_path, image_paths, config=self.config)
            verify_reviews(json.loads(row['draft_json']), hashes,config=self.config)
            store.claim_create(post_id, payload)
        async with browser as admin:
            await admin.ready()
            if op:
                # A create intent survives an unknown outcome. Never issue another POST.
                row = self.state.get_post(post_id)
                if row['admin_id']:
                    actual = await admin.read(row['admin_id'])
                    store.record_draft(post_id, actual)
                    return actual
                candidates = await admin.find_drafts(json.loads(op['payload']))
                if len(candidates) != 1:
                    raise AdminError('create_requires_manual_reconciliation')
                store.record_draft(post_id, candidates[0])
                return candidates[0]
            admin.guard.check_action = lambda: store.check_write(post_id)
            admin.file_hashes = dict(zip([str(Path(p).resolve()) for p in [cover_path, *image_paths]], hashes))
            actual = await admin.save_draft(payload, lambda value: store.materialized(post_id, value),
                                            cover_path=cover_path, image_paths=image_paths)
            store.record_draft(post_id, actual)
            return actual

    def recover_interrupted_publish(self):
        """After a restart a leftover `before_publish` becomes `uncertain` (read-only readback follows), but only
        when the admin profile lock is free: a held lock means another process may be publishing right now."""
        if self.config.mode != 'live' or not self.state.list_posts(('before_publish',)):
            return []
        raw = self.config.admin.profile_path
        profile = Path(raw) if raw else None
        if profile is None or not profile.is_absolute() or profile.is_symlink():
            return []
        moved = []
        try:
            profile.mkdir(parents=True, exist_ok=True, mode=0o700)
            with ProcessLock(profile):
                for row in self.state.list_posts(('before_publish',)):
                    self.state.transition(row['id'], 'uncertain', 'publish_interrupted_restart')
                    moved.append(row['id'])
        except AlreadyRunning:
            LOG.warning('NailBlog restart recovery skipped: admin profile is in use')
        except OSError as error:
            LOG.warning('NailBlog restart recovery skipped exception=%s', type(error).__name__)
        return moved

    def _interrupt(self, post_id, reason):
        """A claimed publish whose outcome is unknown is `uncertain`; an unclaimed (`scheduled`) post is untouched."""
        current = self.state.get_post(post_id)
        if current and current['state'] == 'before_publish':
            self.state.transition(post_id, 'uncertain', reason)

    def _backoff_active(self, row, now):
        if row['state'] != 'uncertain':
            return False
        return self.readback_backoff.get(row['id'], (0, 0))[1] > now

    def _note_timeout(self, post_id, now):
        failures = self.readback_backoff.get(post_id, (0, 0))[0] + 1
        delay = min(READBACK_BACKOFF_SECONDS * 2 ** (failures - 1), READBACK_BACKOFF_MAX_SECONDS)
        self.readback_backoff[post_id] = (failures, now + delay)

    async def _refusal_notice(self, row, error):
        from .publication import AdminError
        code = str(error) if isinstance(error, AdminError) else ''
        if code not in REFUSAL_TEXT or self.notice_send is None:
            return
        await send_once(self.state, self.notice_send, notice_key('publish_refused', f"{row['id']}|{row['slot_at']}|{code}"),
                        f"NailBlog: bài #{row['id']} (slot {row['slot_at']}) {REFUSAL_TEXT[code]}", now=self.clock())

    async def _acquire_browser(self, stop):
        """Take `browser_lock`; give up (False) when `stop` fires first."""
        if stop is None:
            await self.browser_lock.acquire()
            return True
        acquire = asyncio.ensure_future(self.browser_lock.acquire())
        waiter = asyncio.ensure_future(stop.wait())
        try:
            await asyncio.wait({acquire, waiter}, return_when=asyncio.FIRST_COMPLETED)
        except BaseException:
            acquire.cancel()
            waiter.cancel()
            if acquire.done() and not acquire.cancelled() and acquire.exception() is None:
                self.browser_lock.release()
            raise
        waiter.cancel()
        if not acquire.done():
            acquire.cancel()
            await asyncio.gather(acquire, return_exceptions=True)
        return not acquire.cancelled()

    async def publish_pass(self, *, post_id=None, stop=None):
        """Due scheduled posts first, then recovery. Durable claims are the only path to a Publish click.

        Each post gets one bounded browser block (`admin.publish_timeout_seconds`) that also ends when `stop` fires."""
        if self.config.mode != 'live' or self.state.control()['paused']:
            return []
        from .admin_state import AdminStore
        store = AdminStore(self.state, self.config)
        now = self.clock()
        candidates = self.due_posts()
        candidates += [row for row in self.state.list_posts(('before_publish', 'uncertain'))
                       if post_id is not None or not self._backoff_active(row, now)]
        if post_id is not None and not any(row['id'] == post_id for row in candidates):
            from .staging_immediate import explicit_publish_candidate
            explicit = explicit_publish_candidate(self.state, self.config, post_id, self.clock())
            if explicit is not None:
                candidates.append(explicit)
        if post_id is not None:
            candidates = [row for row in candidates if row['id'] == post_id]
        completed = []
        for row in candidates:
            if stop is not None and stop.is_set():
                break
            if not await self._acquire_browser(stop):
                break
            try:
                if await self._publish_one(row, store, stop):
                    completed.append(row['id'])
            finally:
                self.browser_lock.release()
        return completed

    async def _publish_one(self, row, store, stop):
        from .admin import Admin
        from .publication import AdminError
        from .admin_images import verify_reviews
        if not store.operation(row['id']) or not row['admin_id']:
            return False
        limit = None
        try:
            payload = store.recovery_payload(row['id'])
            if row['state'] == 'scheduled' and (han_fields(payload) or han_fields(json.loads(row['draft_json']))):
                raise AdminError('unexpected_han_requires_review')
            document = json.loads(row['draft_json'])
            if row['state'] == 'scheduled' and document.get('test_image_waiver'):
                verify_reviews(document, document.get('image_hashes', []), config=self.config)
            async with asyncio.timeout(self.config.admin.publish_timeout_seconds) as limit:
                watcher = None
                if stop is not None:
                    async def stop_deadline():
                        await stop.wait()
                        limit.reschedule(asyncio.get_running_loop().time())
                    watcher = asyncio.create_task(stop_deadline())
                try:
                    async with Admin(self.config) as admin:
                        await admin.ready()
                        # Bind remote MIME/byte checks to the reviewed document on
                        # both fresh publication and read-only uncertain recovery.
                        admin.approved_image_hashes = document.get('image_hashes')
                        if row['state'] == 'scheduled':
                            admin.guard.check_action = lambda: store.check_publish_write(row['id'], self.clock())
                            await admin.publish(row['admin_id'], payload,
                                                lambda: store.claim_publish(row['id'], self.clock()))
                        evidence = await admin.published_readback(row['admin_id'], payload)
                        self.state.transition(row['id'], 'published', evidence=evidence)
                finally:
                    if watcher is not None:
                        watcher.cancel()
            self.readback_backoff.pop(row['id'], None)
            return True
        except asyncio.CancelledError:
            self._interrupt(row['id'], 'publish_interrupted')
            raise
        except Exception as error:
            # Never log raw Playwright/API error text: it may include request contents.
            timed_out = isinstance(error, TimeoutError) and limit is not None and limit.expired()
            if timed_out:
                reason = 'publish_interrupted' if stop is not None and stop.is_set() else 'publish_readback_timeout'
                self._note_timeout(row['id'], self.clock())
            else:
                reason = 'publish_readback_unconfirmed'
            self._interrupt(row['id'], reason)
            LOG.warning('NailBlog admin post=%s error=%s', row['id'],
                        str(error) if isinstance(error, AdminError) else type(error).__name__)
            await self._refusal_notice(row, error)
            return False

    def text_model(self, stop=None):
        return self.text or TextModel(self.config, self.model_call, stop=stop)

    def _start_jobs(self, stop):
        """Trend poll and daily steps run in their own task so a long article never delays the minute tick.
        Only after the Owner is verified (a sender is bound) and not while paused; never two passes at once."""
        if self.notice_send is None or self.state.control()["paused"] or (self._jobs and not self._jobs.done()):
            return
        self._jobs = asyncio.create_task(daily.run_due(self, stop))

    async def scheduler(self, stop):
        try:
            if self.config.mode == 'live':
                try:
                    self.recover_interrupted_publish()
                except Exception as error:
                    LOG.warning("NailBlog restart recovery failed exception=%s", type(error).__name__)
            while not stop.is_set():
                try:
                    self.tick()
                    if self.config.mode == 'live':
                        await self.publish_pass(stop=stop)
                    self.usage.prune()
                    await self.notify()
                    self._start_jobs(stop)
                except Exception as error:
                    LOG.warning("NailBlog scheduler pass failed exception=%s", type(error).__name__)
                await wait_for_stop(stop, TICK_SECONDS)
        finally:
            if self._jobs is not None:
                await asyncio.gather(self._jobs, return_exceptions=True)

    # Local cache of what the last plan-week read, so preview needs no network and a failing source has a fallback.
    @property
    def cache_path(self):
        return Path(self.config.db_path).parent / CACHE_NAME

    def load_cache(self):
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) and isinstance(data.get("posts"), list) else None

    def save_cache(self, items, posts):
        write_atomic(self.cache_path, {"savedAt": datetime.fromtimestamp(self.clock(), timezone.utc).isoformat(),
                                       "items": items, "posts": posts})

    def read_material(self, opener, cache, today=None):
        """Feeds + published posts (GET only), falling back to `cache` when a source fails. -> (items, posts, notes)."""
        notes = []
        timeout = self.config.sources.timeout_seconds
        if self.config.sources.feeds:
            items, feed_notes = fetch_feeds(self.config.sources.feeds, opener=opener, timeout=timeout)
            notes += feed_notes
        else:
            items = []
            notes.append("chưa cấu hình nguồn RSS ([sources] feeds)")
        if not items and cache.get("items"):
            items = cache["items"]
            notes.append("dùng tư liệu đã lưu từ lần trước")
        try:
            posts = fetch_posts(self.config.contract.api_origin, opener=opener, timeout=timeout, today=today)
        except SourceError as error:
            if not cache.get("posts"):
                raise SourceError(f"cannot read published posts and no cache exists: {error}") from None
            posts = cache["posts"]
            notes.append(f"không đọc được bài đã đăng ({error}), dùng danh sách đã lưu")
        return items, posts, notes

    def save_articles_as_snapshots(self, items, now):
        """Keep each beauty-RSS article once in trend_snapshots (source rss:<host>) so its words count as daily
        mentions in the trend score."""
        by_host = {}
        for item in items:
            host = (urlparse(item["url"]).hostname or "").removeprefix("www.")
            news = [{"title": item.get("snippet", "")[:300], "url": item["url"]}] if item.get("snippet") else []
            by_host.setdefault(host, []).append((item["title"], 0, news))
        for host, rows in by_host.items():
            self.state.add_trend_snapshots(now, "rss:" + host, rows, skip_known=True)

    async def plan_week(self, text, *, opener=None, dry_run=False, today=None):
        """Read sources, ask the planner, store `proposed` topics and build the DM.

        The DM goes out through the bound Telegram sender; without one (CLI) it is returned for printing.
        `dry_run` never sends."""
        today = today or datetime.fromtimestamp(self.clock(), VN_TZ).date()
        week_start = next_week_start(today)
        label = week_label(week_start)
        if any(row["week"] == label for row in self.state.list_topics(("proposed", "approved"))):
            return PlanOutcome(f"NailBlog: tuần {label} đã có chủ đề, không tạo lại. Dùng /nailblog topics để xem.",
                               created=False)
        items, posts, notes = self.read_material(opener, self.load_cache() or {}, today)
        self.save_cache(items, posts)
        topics, dropped = await propose(text, items, posts, week_start, today, self.config)
        if dropped:
            notes.append(f"đã loại {len(dropped)} chủ đề (nguồn không có trong tư liệu hoặc trùng bài 90 ngày)")
        needed = WEEK_DAYS * len(slots_of_config(self.config))
        if len(topics) < needed:
            notes.append(f"chỉ còn {len(topics)} chủ đề, chưa đủ {needed} slot")
        placed = sum(1 for topic in topics if topic["slot_at"])
        if topics and placed < needed:
            notes.append(f"{placed}/{needed} slot đã có chủ đề")
        ids = store_topics(self.state, week_start, topics, items)
        message = format_topics_dm([self.state.get_topic(i) for i in ids], week_start, notes)
        self.state.set_last_result(f"plan-week {label}: {len(ids)} chủ đề")
        sent = False
        if not dry_run and self.notice_send is not None:
            sent = await send_once(self.state, self.notice_send, notice_key("topics", label), message,
                                   now=self.clock())
        return PlanOutcome(message, ids, sent)

    def _occupied_slots(self):
        rows = []
        for post in self.state.list_posts(OCCUPYING_STATES):
            try:
                promo = bool(json.loads(post["draft_json"]).get("product_promo"))
            except (ValueError, AttributeError):
                promo = False
            rows.append({"slot_at": post["slot_at"], "product_promo": promo})
        return rows

    def _assess(self, article, topic, promo, sources, posts, today):
        site = self.config.contract.site_origin
        images = placeholder_images(article)
        content = render_html(article, images, site_origin=site)
        ungrounded = facts.check(article, sources)
        result = gate(article, category=topic["category"], slot_at=topic["slot_at"], product_promo=promo,
                      existing_slugs=[post.get("slug") for post in posts] if posts is not None else None,
                      others=self._occupied_slots(), ungrounded=ungrounded, site_origin=site, posts=posts,
                      today=today, content=content)
        result = hold_unexpected_han(article, result)
        return images, content, ungrounded, result

    async def draft(self, topic, text, *, posts, today):
        """One bounded editorial repair, including alt-length holds; other holds remain blocking.

        The unchanged complete gate and facts check decide the result after the repair."""
        sources, promo, _scores = topic_meta(topic)
        try:
            metadata = json.loads(topic.get('sources_json', '{}'))
        except (TypeError, ValueError):
            metadata = {}
        hints = {key: metadata[key] for key in ('primary_keyword', 'secondary_keywords')
                 if isinstance(metadata, dict) and key in metadata}
        article = await write_article(
            text, {"title": topic["title"], "category": topic["category"], "product_promo": promo, **hints}, sources,
            site_origin=self.config.contract.site_origin, today=today, related_posts=posts,
            links=self.config.links)
        article, repaired = rebalance_paragraphs(article, sources)
        images, content, ungrounded, result = self._assess(article, topic, promo, sources, posts, today)
        if any(issue.code == 'wall_of_text' for issue in result.issues):
            article, wall_repaired = rebalance_paragraphs(article, sources, wall=True)
            repaired += wall_repaired
            images, content, ungrounded, result = self._assess(article, topic, promo, sources, posts, today)
        repair_issues = editorial_repair_issues(result, article)
        if repair_issues:
            try:
                article, model_repaired = await repair_article(text, article, repair_issues, sources=sources)
                article, balanced = rebalance_paragraphs(article, sources)
                repaired += model_repaired + balanced
            except TextModelError as error:
                LOG.warning("NailBlog repair failed reason=%s", error)
                article = {**article, "writer_audit": list(article.get("writer_audit", [])) +
                           ["repair_model_error:TextModelError"]}
            else:
                images, content, ungrounded, result = self._assess(article, topic, promo, sources, posts, today)
            trimmed = trim_list_items(article, sources)
            if trimmed is not article:
                article = trimmed
                repaired += ['trim_items']
                images, content, ungrounded, result = self._assess(article, topic, promo, sources, posts, today)
        article = annotate_repair_outcome(article, result)
        article = {**article, "jsonld": build_jsonld(article, self.config.contract.site_origin, topic["slot_at"])}
        return Draft(article, content, images, ungrounded, result, word_count(article), repaired)

    @staticmethod
    def document(draft, topic):
        return {**draft.article, "topic_id": topic["id"], "slot_at": topic["slot_at"], "content_html": draft.content,
                "images": draft.images, "facts": {"ungrounded": draft.ungrounded}, "quality": draft.result.as_dict(),
                "words": draft.words, "mode": "preview", "repaired": draft.repaired}

    @staticmethod
    def write_files(draft, document, out_dir):
        """article.json and article.html in `out_dir`; returns their paths as text."""
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        json_path, html_path = out / "article.json", out / "article.html"
        json_path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
        html_path.write_text(
            '<!doctype html>\n<html lang="vi"><head><meta charset="utf-8"><title>' + html.escape(draft.article["title"])
            + "</title>" + PREVIEW_STYLE + "</head><body>\n<h1>" + html.escape(draft.article["title"]) + "</h1>\n" + draft.content
            + "\n</body></html>\n", encoding="utf-8")
        return str(json_path), str(html_path)

    async def preview(self, topic_id, out_dir, text):
        """Write one article for a topic to `out_dir`: article.json and article.html. No website call, no images."""
        topic = self.state.get_topic(topic_id)
        if topic is None or topic["state"] == "dropped":
            raise PreviewError(f"topic {topic_id} not found or dropped")
        cache = self.load_cache()
        today = datetime.fromtimestamp(self.clock(), VN_TZ).date()
        draft = await self.draft(topic, text, posts=cache["posts"] if cache else None, today=today)
        json_path, html_path = self.write_files(draft, self.document(draft, topic), out_dir)
        return PreviewOutcome(draft.result.verdict, draft.words, [issue.as_dict() for issue in draft.result.issues],
                              json_path, html_path, draft.repaired)
