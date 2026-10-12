"""Durable optional worker/scheduler; Save and Approve are never blindly retried."""

import asyncio
import json
import logging

from .browser import Browser, matches_expected
from .model import NailHubModel
from .state import State
from .validation import NeedsHuman, proposal

LOG = logging.getLogger(__name__)


async def model_call(function, *args):
    # Cancelling to_thread does not stop HTTP. Finish it before closing worker resources.
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


class Service:
    def __init__(self, config, *, state=None, model=None, browser_factory=Browser):
        self.config = config
        self.state = state or State(config.db_path)
        self.model = model or NailHubModel(config)
        self.browser_factory = browser_factory

    def command(self, command):
        if command == 'scan':
            added = self.state.enqueue()
            return 'NailHub scan queued.' if added else 'NailHub scan already queued/running.'
        if command == 'pause':
            self.state.pause(True)
            return 'NailHub paused. An in-flight remote action is reconciled; no next action starts.'
        if command == 'resume':
            self.state.pause(False)
            self.state.enqueue()
            return 'NailHub resumed; scan queued.'
        if command == 'status':
            status = self.state.status()
            counts = ', '.join(f'{k}={v}' for k, v in sorted(status['counts'].items())) or 'no posts'
            return (f"NailHub {self.config.mode}; paused={bool(status['paused'])}; "
                    f"queued={bool(status['pending'])}; running={bool(status['running'])}; {counts}; "
                    f"last={status['last_result']}")
        raise ValueError('unsupported NailHub command')

    def check_pause(self):
        if self.state.status()['paused']:
            raise NeedsHuman('paused by Owner')

    async def reconcile(self, browser):
        for job in self.state.interrupted():
            try:
                actual = await browser.read(job['id'])
                expected = json.loads(job['expected'])
                if job['state'] == 'before_approve' and matches_expected(actual, expected, status=self.config.approved_status):
                    observed = {**actual.snapshot(), 'audit': expected.get('audit', {})}
                    self.state.mark(job['id'], 'approved', 'approval established by fresh read after restart', expected=observed)
                elif matches_expected(actual, expected, status=self.config.draft_status):
                    # Saved data is known, but human review still owns continuation after a crash.
                    self.state.mark(job['id'], 'needs-human', 'recovered Draft matches proposal; manual continuation required')
                else:
                    self.state.mark(job['id'], 'uncertain', 'mutation outcome cannot be established after restart')
            except Exception:
                self.state.mark(job['id'], 'uncertain', 'crash reconciliation read failed')

    async def process(self, browser, post):
        browser.check_action = self.check_pause
        if not self.state.claim_post(post.id):
            return
        try:
            self.check_pause()
            if post.status != self.config.draft_status or post.category != self.config.category:
                raise NeedsHuman('unsupported status or category')
            data = await model_call(self.model.extract, post)
            intended = proposal(post, data, self.config)
            expected = {**post.snapshot(), 'fields': intended['fields'], 'audit': intended}
            if self.config.mode == 'preview':
                self.state.mark(post.id, 'preview', intended['rationale'], expected={**expected, 'audit': intended})
                return
            self.check_pause()
            # Checkpoint BEFORE Edit/AI too: AI is a remote operation and may persist state.
            self.state.mark(post.id, 'before_save', 'Edit/AI/Save may be in flight', expected=expected)
            title, content = await browser.edit_and_rewrite(post, intended['changes'])
            # Owner delegates rewrite quality to NailHub's built-in AI flow.
            # Save/readback still establish that exactly this returned output persisted.
            expected['content'] = content
            expected['fields']['title'] = title
            self.state.mark(post.id, 'before_save', 'site AI output received; Save may be in flight', expected=expected)
            self.check_pause()
            saved = await browser.save(post, expected)
            if not matches_expected(saved, expected, status=self.config.draft_status):
                raise NeedsHuman('post-save fresh read does not match every expected field')
            self.check_pause()
            self.state.mark(post.id, 'before_approve', 'Approve may be in flight', expected=expected)
            approved = await browser.approve(saved)
            if not matches_expected(approved, expected, status=self.config.approved_status):
                raise NeedsHuman('approval fresh read did not establish expected state')
            observed = {**approved.snapshot(), 'audit': intended}
            observed['audit']['approval_publication'] = json.loads(approved.fields.get('_preserved', '{}')).get('publishedAt')
            self.state.mark(post.id, 'approved', 'saved values and approved status verified', expected=observed)
        except Exception as error:
            current = self.state.get(post.id)
            uncertain = current and current['state'] in ('before_save', 'before_approve')
            # Only our bounded fixed validation reasons are retained; external exception bodies never are.
            reason = str(error) if isinstance(error, NeedsHuman) else 'worker operation failed; review required'
            self.state.mark(post.id, 'uncertain' if uncertain else 'needs-human', reason)

    async def scan(self):
        async with self.browser_factory(self.config) as browser:
            await browser.ready()
            await self.reconcile(browser)
            self.check_pause()
            processed = 0
            for candidate in await browser.candidates():
                self.check_pause()
                known = self.state.get(candidate.id)
                requeue_preview = bool(known and known['state'] == 'preview' and self.config.mode == 'live')
                if known and (known['state'] in ('uncertain', 'approved')
                              or (known['hash'] == candidate.fingerprint and known['state'] != 'queued' and not requeue_preview)):
                    continue
                post = await browser.read(candidate.id)
                if self.state.observe(post.id, post.fingerprint, post.snapshot(), requeue_preview=requeue_preview):
                    await self.process(browser, post)
                    processed += 1
                    if processed >= self.config.max_batch:
                        break
            return f'scan complete; processed={processed}'

    async def worker(self, stop):
        self.state.recover_queue()
        while not stop.is_set():
            if self.state.claim_scan():
                try:
                    result = await self.scan()
                except asyncio.CancelledError:
                    # Queue remains running until restart; checkpoint reconciliation owns remote state.
                    raise
                except Exception as error:
                    result = str(error) if isinstance(error, NeedsHuman) else 'scan blocked; browser/model unavailable'
                    LOG.warning('NailHub scan blocked exception=%s', type(error).__name__)
                self.state.finish_scan(result)
            try:
                await asyncio.wait_for(stop.wait(), 1)
            except TimeoutError:
                pass

    async def scheduler(self, stop):
        while not stop.is_set():
            self.state.enqueue()
            try:
                await asyncio.wait_for(stop.wait(), self.config.interval_seconds)
            except TimeoutError:
                pass
