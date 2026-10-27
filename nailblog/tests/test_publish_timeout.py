"""Publish hardening: bounded browser blocks, uncertain-on-interrupt, restart recovery, step logs. No network."""
import asyncio
import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from nailblog import admin as admin_module
from nailblog._runtime import ProcessLock
from nailblog.admin import Admin
from nailblog.admin_state import AdminStore
from nailblog.publication import AdminError
from nailblog.service import Service
from nailblog.state import State
from tests.test_admin_phase2 import POST_ID, SLOT, config, document, payload

OTHER_ID = '12345678-1234-1234-1234-123456789def'
EVIDENCE = {'readback': {'admin_id': POST_ID}}


class FakeAdmin:
    """Stands in for the Playwright adapter: hooks decide where it hangs; `events` records what was called."""

    def __init__(self, events, *, publish=None, readback=None):
        self.events, self.on_publish, self.on_readback = events, publish, readback
        self.guard = SimpleNamespace()
        self.approved_image_hashes = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def ready(self):
        return {}

    async def publish(self, admin_id, expected, before_click):
        self.events.append(('publish', admin_id))
        if self.on_publish:
            await self.on_publish(before_click)
        else:
            before_click()

    async def published_readback(self, admin_id, expected):
        self.events.append(('readback', admin_id))
        if self.on_readback:
            return await self.on_readback(admin_id)
        return {'readback': {'admin_id': admin_id}}


async def hang(*args):
    await asyncio.sleep(3600)


def build(directory, *, timeout=0.2):
    c = config(directory)
    return replace(c, admin=replace(c.admin, publish_timeout_seconds=timeout))


def add_post(state, c, slot=SLOT, admin_id=POST_ID, *, claim=False, uncertain=False):
    store, p = AdminStore(state, c), payload()
    p['publishedAt'] = slot.isoformat()
    n = state.add_post(slot.isoformat(), draft=document(p))
    store.claim_create(n, p)
    store.record_draft(n, {**p, 'id': admin_id})
    store.approve(n, (slot - timedelta(hours=3)).timestamp())
    if claim or uncertain:
        store.claim_publish(n, slot.timestamp())
    if uncertain:
        state.transition(n, 'uncertain', 'test')
    return n


class PublishTimeoutTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.c = build(self.tmp.name)
        self.state = State(self.c.db_path)
        self.now = SLOT.timestamp()
        self.service = Service(self.c, state=self.state, clock=lambda: self.now)
        self.state.arm(self.service.startup_binding)
        self.events = []

    def admin(self, **hooks):
        return patch('nailblog.admin.Admin', side_effect=lambda cfg: FakeAdmin(self.events, **hooks))

    async def test_hang_after_claim_ends_uncertain_and_never_clicks_twice(self):
        n = add_post(self.state, self.c)

        async def claim_then_hang(before_click):
            before_click()
            await hang()
        with self.admin(publish=claim_then_hang):
            self.assertEqual(await self.service.publish_pass(), [])
            self.assertEqual(self.state.get_post(n)['state'], 'uncertain')
            self.assertEqual(self.state.get_post(n)['reason'], 'publish_readback_timeout')
            before = [e for e in self.events if e[0] == 'readback']
            await self.service.publish_pass()  # same clock: backing off, so no readback at all
            self.assertEqual([e for e in self.events if e[0] == 'readback'], before)
            self.assertEqual(self.state.get_post(n)['state'], 'uncertain')
            self.now += 4000
            await self.service.publish_pass()  # backoff over: read-only readback, never a second click
        self.assertEqual([e for e in self.events if e[0] == 'publish'], [('publish', POST_ID)])
        self.assertEqual([e for e in self.events if e[0] == 'readback'], before + [('readback', POST_ID)])
        self.assertEqual(self.state.get_post(n)['state'], 'published')

    async def test_hang_before_claim_stays_scheduled(self):
        n = add_post(self.state, self.c)

        async def hang_before_claim(before_click):
            await hang()
        with self.admin(publish=hang_before_claim):
            await self.service.publish_pass()
        self.assertEqual(self.state.get_post(n)['state'], 'scheduled')

    async def test_cancel_after_claim_ends_uncertain_and_propagates(self):
        n = add_post(self.state, self.c)
        claimed = asyncio.Event()

        async def claim_then_wait(before_click):
            before_click()
            claimed.set()
            await hang()
        with self.admin(publish=claim_then_wait):
            task = asyncio.create_task(self.service.publish_pass())
            await asyncio.wait_for(claimed.wait(), 5)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(self.state.get_post(n)['state'], 'uncertain')
        self.assertEqual(self.state.get_post(n)['reason'], 'publish_interrupted')

    async def test_stop_event_interrupts_the_block(self):
        c = build(self.tmp.name, timeout=60)
        service = Service(c, state=self.state, clock=lambda: self.now)
        self.state.arm(service.startup_binding)
        n = add_post(self.state, c)
        stop, claimed = asyncio.Event(), asyncio.Event()

        async def claim_then_wait(before_click):
            before_click()
            claimed.set()
            await hang()
        with self.admin(publish=claim_then_wait):
            task = asyncio.create_task(service.publish_pass(stop=stop))
            await asyncio.wait_for(claimed.wait(), 5)
            stop.set()
            await asyncio.wait_for(task, 5)
        self.assertEqual(self.state.get_post(n)['state'], 'uncertain')
        self.assertEqual(self.state.get_post(n)['reason'], 'publish_interrupted')

    async def test_restart_turns_before_publish_uncertain_then_reads_back_without_click(self):
        n = add_post(self.state, self.c, claim=True)
        self.assertEqual(self.state.get_post(n)['state'], 'before_publish')
        self.assertEqual(self.service.recover_interrupted_publish(), [n])
        self.assertEqual(self.state.get_post(n)['state'], 'uncertain')
        self.assertEqual(self.state.get_post(n)['reason'], 'publish_interrupted_restart')
        with self.admin():
            self.assertEqual(await self.service.publish_pass(), [n])
        self.assertEqual(self.state.get_post(n)['state'], 'published')
        self.assertEqual(self.events, [('readback', POST_ID)])

    async def test_restart_recovery_needs_the_profile_lock(self):
        n = add_post(self.state, self.c, claim=True)
        profile = Path(self.c.admin.profile_path)
        profile.mkdir(parents=True, exist_ok=True)
        with ProcessLock(profile):
            self.assertEqual(self.service.recover_interrupted_publish(), [])
        self.assertEqual(self.state.get_post(n)['state'], 'before_publish')

    async def test_due_post_runs_before_slow_uncertain_one(self):
        old_slot = SLOT - timedelta(days=1)
        slow = add_post(self.state, self.c, old_slot, OTHER_ID, uncertain=True)
        due = add_post(self.state, self.c, SLOT, POST_ID)

        async def slow_readback(admin_id):
            if admin_id == OTHER_ID:
                await hang()
            return {'readback': {'admin_id': admin_id}}
        with self.admin(readback=slow_readback):
            done = await self.service.publish_pass()
        self.assertEqual(self.events[0], ('publish', POST_ID))
        self.assertIn(('readback', OTHER_ID), self.events)
        self.assertEqual(done, [due])
        self.assertEqual(self.state.get_post(slow)['state'], 'uncertain')

    async def test_refusals_send_one_owner_dm_each(self):
        for code in ('daily_publish_limit', 'outside_publish_window'):
            with self.subTest(code=code):
                n = add_post(self.state, self.c, SLOT + timedelta(days=1 if code == 'daily_publish_limit' else 2),
                             OTHER_ID if code == 'daily_publish_limit' else POST_ID)
                self.now = (SLOT + timedelta(days=1 if code == 'daily_publish_limit' else 2)).timestamp()
                sender = AsyncMock()
                self.service.notice_send = sender

                async def refuse(before_click, code=code):
                    raise AdminError(code)
                with self.admin(publish=refuse):
                    await self.service.publish_pass()
                    await self.service.publish_pass()
                self.assertEqual(sender.await_count, 1)
                self.assertIn(f'#{n}', sender.await_args.args[0])
                self.assertEqual(self.state.get_post(n)['state'], 'scheduled')

    async def test_hung_draft_save_is_bounded_and_frees_the_browser(self):
        n = add_post(self.state, self.c)

        async def hung_save(*args, **kwargs):
            await hang()
        self.assertEqual(self.c.admin.publish_timeout_seconds, 0.2)  # the inner bound under test
        loop = asyncio.get_running_loop()
        started = loop.time()
        with patch.object(self.service, '_save_remote_draft', hung_save):
            with self.assertRaises(TimeoutError):
                # The outer bound is far larger: only the service's own timeout can end this call early.
                await asyncio.wait_for(self.service.save_remote_draft(n, payload(), cover_path='x', image_paths=[]), 30)
        self.assertLess(loop.time() - started, 2)
        self.assertFalse(self.service.browser_lock.locked())
        with self.admin():
            self.assertEqual(await self.service.publish_pass(), [n])

    async def test_waiting_for_the_browser_gives_up_on_stop(self):
        add_post(self.state, self.c)
        stop = asyncio.Event()
        await self.service.browser_lock.acquire()
        with self.admin():
            task = asyncio.create_task(self.service.publish_pass(stop=stop))
            await asyncio.sleep(0.05)
            self.assertFalse(task.done())
            stop.set()
            self.assertEqual(await asyncio.wait_for(task, 5), [])
        self.assertEqual(self.events, [])
        self.assertTrue(self.service.browser_lock.locked())
        self.service.browser_lock.release()

    async def test_browser_lock_and_media_hook_exist(self):
        self.assertIsInstance(self.service.browser_lock, asyncio.Lock)
        self.assertIsNone(self.service.media_send)


class StepLogTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.c = build(self.tmp.name)

    async def test_steps_log_name_outcome_elapsed_without_urls(self):
        admin = Admin(self.c)
        expected = payload()
        with patch.object(admin_module, 'public_webp', return_value={}), \
                self.assertLogs('nailblog.admin', 'INFO') as logs:
            admin.step_post = POST_ID
            await admin.image_bytes(expected)
            admin.approved_image_hashes = ['a' * 64]
            with self.assertRaises(AdminError):
                await admin.image_bytes(expected)
            with patch.object(admin, 'write_ready', side_effect=AdminError('verified_contract_required')):
                with self.assertRaises(AdminError):
                    await admin.publish(POST_ID, expected, lambda: None)
        text = '\n'.join(logs.output)
        self.assertIn(f'NailBlog publish post={POST_ID} step=image_bytes outcome=ok elapsed_ms=', text)
        self.assertIn('step=image_bytes outcome=approved_image_hashes_invalid', text)
        self.assertIn('step=publish outcome=verified_contract_required', text)
        self.assertNotIn('https://', text)
        self.assertNotIn('http', text.replace('NailBlog publish', ''))

    async def test_cancelled_step_is_logged_as_cancelled(self):
        admin = Admin(self.c)
        admin.ready = None
        with patch.object(admin, 'write_ready'), patch.object(admin, 'read', side_effect=hang), \
                self.assertLogs('nailblog.admin', 'INFO') as logs:
            with self.assertRaises(TimeoutError):
                async with asyncio.timeout(0.1):
                    await admin.publish(POST_ID, payload(), lambda: None)
        self.assertIn('step=publish outcome=cancelled', '\n'.join(logs.output))

    async def test_close_steps_are_bounded(self):
        admin = Admin(self.c)
        released = []
        admin.context = SimpleNamespace(close=hang)
        admin.manager = SimpleNamespace(stop=hang)
        admin.lock = SimpleNamespace(__exit__=lambda *a: released.append(1))
        with patch.object(admin_module, 'CLOSE_TIMEOUT_SECONDS', 0.05), \
                self.assertLogs('nailblog.admin', 'WARNING') as logs:
            await asyncio.wait_for(admin.__aexit__(), 5)
        self.assertEqual(released, [1])
        self.assertIn('step=context_close outcome=close_timeout', '\n'.join(logs.output))
        self.assertIn('step=manager_stop outcome=close_timeout', '\n'.join(logs.output))


if __name__ == '__main__':
    unittest.main()
