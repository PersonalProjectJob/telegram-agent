import asyncio
import contextlib
import io
import json
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from nailblog import __main__ as cli
from nailblog import service as service_module
from nailblog.model_lock import MODEL_LOCK
from nailblog.notices import notice_key, send_once, uncertain_notice
from nailblog.receipt import ready
from nailblog.schedule import VN_TZ
from nailblog.service import Service
from nailblog.state import State
from tests.helpers import settings, write_config

SLOT_TEXT = "2026-10-17T06:00+07:00"
SLOT = datetime(2026, 10, 17, 6, 0, tzinfo=VN_TZ).timestamp()


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = settings(self.directory.name)
        self.state = State(self.config.db_path)
        self.now = SLOT + 60
        self.service = Service(self.config, state=self.state, clock=lambda: self.now)

    def test_due_scheduled_post_is_only_logged_and_never_changes_state(self):
        post = self.state.add_post(SLOT_TEXT, state="scheduled")
        with self.assertLogs("nailblog.service", "INFO") as logs:
            self.assertEqual(self.service.tick(), [post])
        self.assertIn(f"preview: would publish {post}", logs.output[0])
        self.assertEqual(self.state.get_post(post)["state"], "scheduled")

    def test_each_due_post_is_announced_once_per_process(self):
        self.state.add_post(SLOT_TEXT, state="scheduled")
        with self.assertLogs("nailblog.service", "INFO") as logs:
            self.service.tick()
            self.service.tick()
        self.assertEqual(len(logs.output), 1)

    def test_window_edges_and_other_states(self):
        scheduled = self.state.add_post(SLOT_TEXT, state="scheduled")
        for state in ("review", "held", "before_publish", "published", "uncertain"):
            self.state.add_post(SLOT_TEXT, state=state)
        self.now = SLOT - 1
        self.assertEqual(self.service.tick(), [])
        self.now = SLOT + 15 * 60
        self.assertEqual(self.service.tick(), [scheduled])
        self.now = SLOT + 15 * 60 + 1
        self.assertEqual(self.service.tick(), [])
        self.assertEqual(len(self.state.list_posts(("scheduled",))), 1)

    def test_paused_service_does_nothing(self):
        self.state.add_post(SLOT_TEXT, state="scheduled")
        self.state.set_paused(True)
        self.assertEqual(self.service.tick(), [])

    def test_live_tick_announces_but_does_not_bypass_guarded_publish(self):
        service = Service(replace(self.config, mode="live"), state=self.state, clock=lambda: self.now)
        post = self.state.add_post(SLOT_TEXT, state="scheduled")
        with self.assertLogs("nailblog.service", "INFO") as logs:
            self.assertEqual(service.tick(), [post])
        self.assertIn("awaits guarded publish pass", logs.output[0])
        self.assertEqual(self.state.get_post(post)["state"], "scheduled")

    def test_tick_refreshes_the_usage_summary_file(self):
        self.service.tick()
        self.assertTrue((Path(self.directory.name) / "agentfleet-usage.json").exists())


class ModelCallTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_calls_enter_the_shared_lock_as_background(self):
        with tempfile.TemporaryDirectory() as directory:
            service = Service(settings(directory))
            spy = Mock(wraps=MODEL_LOCK)
            original = service_module.MODEL_LOCK
            service_module.MODEL_LOCK = spy
            try:
                self.assertEqual(await service.model_call(lambda a, b: a + b, 2, 3), 5)
            finally:
                service_module.MODEL_LOCK = original
            spy.enter.assert_called_once_with(background=True, stop=None)

    async def test_model_call_errors_propagate_after_releasing_the_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            service = Service(settings(directory))

            def boom():
                raise OSError("x")
            with self.assertRaises(OSError):
                await service.model_call(boom)
            self.assertEqual(await service.model_call(lambda: "again"), "again")

    async def test_scheduler_stops_when_asked(self):
        with tempfile.TemporaryDirectory() as directory:
            service = Service(settings(directory))
            stop = asyncio.Event()
            task = asyncio.create_task(service.scheduler(stop))
            await asyncio.sleep(0.05)
            stop.set()
            await asyncio.wait_for(task, 5)


class NoticeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state = State(Path(self.directory.name) / "n.sqlite3")

    async def test_send_once_never_duplicates(self):
        send = AsyncMock()
        key = notice_key("k", 1)
        self.assertTrue(await send_once(self.state, send, key, "hello", now=100))
        self.assertFalse(await send_once(self.state, send, key, "hello", now=10 ** 6))
        send.assert_awaited_once_with("hello")

    async def test_failed_send_is_retried_after_backoff_then_stops(self):
        send = AsyncMock(side_effect=[OSError("net"), None])
        key = notice_key("k", 2)
        with self.assertLogs("nailblog.notices", "WARNING"):
            self.assertFalse(await send_once(self.state, send, key, "x", now=100))
        self.assertFalse(await send_once(self.state, send, key, "x", now=200))
        self.assertEqual(send.await_count, 1)
        self.assertTrue(await send_once(self.state, send, key, "x", now=401))
        self.assertFalse(await send_once(self.state, send, key, "x", now=10 ** 6))
        self.assertEqual(send.await_count, 2)

    async def test_uncertain_posts_notify_the_owner_once_without_draft_content(self):
        post = self.state.add_post(SLOT_TEXT, state="uncertain", draft={"title": "SECRET DRAFT TITLE"})
        service = Service(settings(self.directory.name), state=self.state, clock=lambda: 500.0)
        service.notice_send = AsyncMock()
        await service.notify()
        await service.notify()
        service.notice_send.assert_awaited_once()
        text = service.notice_send.await_args.args[0]
        self.assertIn(f"#{post}", text)
        self.assertNotIn("SECRET", text)
        self.state.add_post(SLOT_TEXT, state="review")
        self.assertIsNone(uncertain_notice(self.state.list_posts(("review",))[0]))
        silent = Service(settings(self.directory.name), state=self.state)
        await silent.notify()  # no sender bound: nothing happens, nothing raises


class ReceiptAndCliTests(unittest.TestCase):
    def test_ready_receipt_needs_a_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            ready(settings(directory))
            self.assertFalse((Path(directory) / "agentfleet-ready.json").exists())

    @unittest.skipUnless(os.name == "nt", "receipt chowns to the tgnailblog group on POSIX")
    def test_ready_receipt_content(self):
        with tempfile.TemporaryDirectory() as directory:
            ready(settings(directory, console_revision="r1"))
            data = json.loads((Path(directory) / "agentfleet-ready.json").read_text(encoding="utf-8"))
            self.assertEqual((data["revision"], data["mode"], data["pid"]), ("r1", "preview", os.getpid()))

    def run_cli(self, *argv):
        old = os.umask(0o022)
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                code = cli.main(list(argv))
            return code, out.getvalue()
        finally:
            os.umask(old)

    def test_status_prints_state_and_config_errors_exit_2(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_config(directory, schedule='image_mode = "random"')
            code, output = self.run_cli("status", "--config", str(path))
            self.assertEqual(code, 0)
            self.assertIn("mode=preview", output)
            self.assertIn("ảnh=random", output)
            bad = write_config(directory, {"owner_ids": []})
            code, output = self.run_cli("status", "--config", str(bad))
            self.assertEqual(code, 2)
            self.assertIn("owner_ids", output)
            code, _ = self.run_cli("status", "--config", str(Path(directory) / "missing.toml"))
            self.assertEqual(code, 2)

    def test_unknown_subcommand_is_rejected_by_the_parser(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            cli.main(["not-a-command", "--config", "/x"])
        self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
