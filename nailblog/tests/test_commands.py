import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from nailblog import telegram
from nailblog.commands import USAGE, execute, make_owner_notice_sender, parse, route_command, verify_owner
from nailblog.schedule import VN_TZ
from nailblog.service import Service
from nailblog.state import State
from tests.helpers import OWNER, dm, settings

NOW = datetime(2026, 10, 17, 3, 0, tzinfo=VN_TZ).timestamp()
SLOT = "2026-10-17T06:00+07:00"


class RoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_owner_dm_command_is_parsed_and_answered_in_the_same_dm(self):
        service = SimpleNamespace(command=Mock(return_value=["ok"]))
        event = dm(raw_text="/nailblog approve 7")
        self.assertTrue(await route_command(event, service, (OWNER,)))
        service.command.assert_called_once_with("approve", (7,))
        event.reply.assert_awaited_once_with("ok", parse_mode=None, link_preview=False)

    async def test_multiple_replies_are_sent_in_order(self):
        service = SimpleNamespace(command=Mock(return_value=["a", "b"]))
        event = dm(raw_text="/nailblog queue")
        await route_command(event, service, (OWNER,))
        self.assertEqual([c.args[0] for c in event.reply.await_args_list], ["a", "b"])

    async def test_wrong_sender_group_forwarded_and_metadata_cases_are_consumed_silently(self):
        variants = [{"sender_id": 99}, {"chat_id": 99}, {"out": True}, {"out": None},
                    {"is_private": False, "is_group": True, "chat_id": -100}, {"is_channel": True},
                    {"fwd_from": object()}, {"message": SimpleNamespace(fwd_from=object())},
                    {"sender_id": True}, {"chat_id": str(OWNER)}, {"is_private": None}]
        for value in variants:
            with self.subTest(value=value):
                service = SimpleNamespace(command=Mock())
                event = dm(**value)
                self.assertTrue(await route_command(event, service, (OWNER,)))
                service.command.assert_not_called()
                event.reply.assert_not_awaited()

    async def test_bot_self_chat_lookup_failure_and_empty_allowlist_are_denied(self):
        cases = [(dm(get_sender=AsyncMock(return_value=SimpleNamespace(id=OWNER, bot=True))), (OWNER,)),
                 (dm(get_chat=AsyncMock(return_value=SimpleNamespace(id=OWNER, bot=False, is_self=True))), (OWNER,)),
                 (dm(get_sender=AsyncMock(side_effect=OSError())), (OWNER,)),
                 (dm(get_sender=AsyncMock(return_value=SimpleNamespace(id=1, bot=False))), (OWNER,)),
                 (dm(), ())]
        for event, ids in cases:
            service = SimpleNamespace(command=Mock())
            self.assertTrue(await route_command(event, service, ids))
            service.command.assert_not_called()
            event.reply.assert_not_awaited()

    async def test_other_namespaces_and_ordinary_messages_are_ignored_not_consumed(self):
        for text in ("/nailhub status", "/nailhub scan", "hello", "", "nailblog status", "/nailblogger status",
                     "please run /nailblog status"):
            with self.subTest(text=text):
                service = SimpleNamespace(command=Mock())
                event = dm(raw_text=text)
                self.assertFalse(await route_command(event, service, (OWNER,)))
                service.command.assert_not_called()
                event.reply.assert_not_awaited()
        self.assertFalse(await route_command(dm(raw_text=None), SimpleNamespace(), (OWNER,)))

    async def test_malformed_namespace_is_consumed_with_usage_and_never_executed(self):
        for text in ("/nailblog", "/nailblog nonsense", "/nailblog status now", "/NAILBLOG status", " /nailblog@other status",
                     "/nailblog approve", "/nailblog approve x", "/nailblog mode weekly"):
            with self.subTest(text=text):
                service = SimpleNamespace(command=Mock())
                event = dm(raw_text=text)
                self.assertTrue(await route_command(event, service, (OWNER,)))
                service.command.assert_not_called()
                event.reply.assert_awaited_once_with(USAGE, parse_mode=None, link_preview=False)

    async def test_unauthorized_malformed_gets_no_usage_hint(self):
        event = dm(raw_text="/nailblog nonsense", sender_id=99, chat_id=99)
        self.assertTrue(await route_command(event, SimpleNamespace(command=Mock()), (OWNER,)))
        event.reply.assert_not_awaited()

    async def test_handler_builder_applies_the_same_gate(self):
        jobs = SimpleNamespace(command=Mock(return_value=["ok"]))
        denied = telegram.make_handler(jobs, ())
        event = dm()
        await denied(event)
        jobs.command.assert_not_called()
        authorized = telegram.make_handler(jobs, (OWNER,))
        await authorized(event)
        jobs.command.assert_called_once_with("status", ())
        for other in (dm(raw_text="ordinary DM"), dm(raw_text="/nailhub status"), dm(sender_id=99, chat_id=99)):
            await authorized(other)
            other.reply.assert_not_awaited()
        self.assertEqual(jobs.command.call_count, 1)

    async def test_owner_binding_requires_username_and_numeric_id_to_match(self):
        config = settings(".", owner_ids=(OWNER,))
        user = SimpleNamespace(id=OWNER, bot=False, username="SotaThao")
        client = SimpleNamespace(get_entity=AsyncMock(return_value=user))
        self.assertEqual(await verify_owner(client, config, SimpleNamespace(id=42)), (OWNER,))
        self.assertEqual(await verify_owner(client, config, SimpleNamespace(id=OWNER)), ())
        client.get_entity = AsyncMock(return_value=SimpleNamespace(id=33, bot=False, username="SotaThao"))
        self.assertEqual(await verify_owner(client, config, SimpleNamespace(id=42)), ())
        client.get_entity = AsyncMock(return_value=SimpleNamespace(id=OWNER, bot=True, username="SotaThao"))
        self.assertEqual(await verify_owner(client, config, SimpleNamespace(id=42)), ())
        self.assertEqual(await verify_owner(client, replace(config, owner_ids=()), SimpleNamespace(id=42)), ())

    async def test_notice_sender_needs_one_verified_owner(self):
        client = SimpleNamespace(send_message=AsyncMock(return_value="sent"))
        send = make_owner_notice_sender(client, (OWNER,))
        self.assertEqual(await send("hi"), "sent")
        client.send_message.assert_awaited_once_with(OWNER, "hi", parse_mode=None, link_preview=False)
        for ids in ((), (1, 2), (True,), (0,)):
            with self.assertRaises(ValueError):
                make_owner_notice_sender(client, ids)


class ParseTests(unittest.TestCase):
    def test_valid_commands(self):
        cases = {
            "/nailblog topics": ("topics", ()),
            "/nailblog topics ok 1,2, 5": ("topics_ok", (1, 2, 5)),
            "/nailblog topics ok 3 3 4": ("topics_ok", (3, 4)),
            "/nailblog topics drop 4": ("topics_drop", (4,)),
            "/nailblog topics edit 2  Thuế   2026  cho tiệm": ("topics_edit", (2, "Thuế 2026 cho tiệm")),
            "/nailblog queue": ("queue", ()),
            "/nailblog approve 12": ("approve", (12,)),
            "/nailblog hold 12 sai   số liệu": ("hold", (12, "sai số liệu")),
            "/nailblog move 3 2026-10-20 19": ("move", (3, date(2026, 10, 20), "19:00")),
            "/nailblog mode random": ("mode", ("random",)),
            "/nailblog pause": ("pause", ()),
            "/nailblog resume": ("resume", ()),
            "/nailblog status": ("status", ()),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parse(text), expected)

    def test_invalid_commands(self):
        for text in ("", "/nailblog", "/nailblog topics ok", "/nailblog topics ok a", "/nailblog topics ok 0",
                     "/nailblog topics drop", "/nailblog topics drop 1 2", "/nailblog topics edit 1",
                     "/nailblog topics edit x y", "/nailblog topics other", "/nailblog approve -1",
                     "/nailblog approve 1 2", "/nailblog approve ١٢", "/nailblog hold 1", "/nailblog hold x why",
                     "/nailblog move 1 2026-13-01 06", "/nailblog move 1 2026-10-20", "/nailblog move 1 2026-10-20 6",
                     "/nailblog move 1 20-10-2026 06", "/nailblog mode", "/nailblog mode fixed random",
                     "/nailblog pause now", "/nailblog status x", "/nailblog topics ok " + ",".join(str(i) for i in range(1, 52)),
                     "/nailblog hold 1 " + "x" * 301, "/nailblog nope"):
            with self.subTest(text=text[:50]):
                self.assertIsNone(parse(text))


class ExecuteTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = settings(self.directory.name)
        self.state = State(self.config.db_path)

    def run_command(self, text, now=NOW):
        parsed = parse(text)
        self.assertIsNotNone(parsed, text)
        return execute(self.state, *parsed, now=now, config=self.config)

    def test_pause_and_resume_change_the_control_row(self):
        self.run_command("/nailblog pause")
        self.assertEqual(self.state.control()["paused"], 1)
        self.run_command("/nailblog resume")
        self.assertEqual(self.state.control()["paused"], 0)

    def test_mode_changes_the_control_row(self):
        for mode in ("random", "adaptive", "fixed"):
            self.run_command(f"/nailblog mode {mode}")
            self.assertEqual(self.state.control()["image_mode"], mode)

    def test_topics_listing_and_curation(self):
        self.assertEqual(self.run_command("/nailblog topics"), ["Chưa có chủ đề nào."])
        ids = [self.state.add_topic("2026-W43", f"Chủ đề {n}", "nail-job") for n in range(1, 5)]
        text = self.run_command("/nailblog topics")[0]
        self.assertTrue(all(f"#{i} [proposed] Chủ đề" in text for i in ids))
        self.run_command(f"/nailblog topics ok {ids[0]},{ids[1]},999")
        self.assertEqual([self.state.get_topic(i)["state"] for i in ids], ["approved", "approved", "proposed", "proposed"])
        self.run_command(f"/nailblog topics drop {ids[2]}")
        self.assertEqual(self.state.get_topic(ids[2])["state"], "dropped")
        self.run_command(f"/nailblog topics edit {ids[3]} Tiêu đề mới")
        self.assertEqual(self.state.get_topic(ids[3])["title"], "Tiêu đề mới")
        self.assertIn("Không", self.run_command(f"/nailblog topics edit {ids[2]} Muộn")[0])
        self.assertEqual(self.state.get_topic(ids[2])["title"], "Chủ đề 3")
        self.assertIn("Không", self.run_command("/nailblog topics drop 999")[0])
        self.assertNotIn("dropped", self.run_command("/nailblog topics")[0])

    def test_topics_ok_does_not_resurrect_a_dropped_topic(self):
        topic = self.state.add_topic("w", "t", "c")
        self.run_command(f"/nailblog topics drop {topic}")
        self.run_command(f"/nailblog topics ok {topic}")
        self.assertEqual(self.state.get_topic(topic)["state"], "dropped")

    def test_approve_moves_review_to_scheduled_only(self):
        review = self.state.add_post(SLOT, state="review")
        self.assertIn("lên lịch", self.run_command(f"/nailblog approve {review}")[0])
        self.assertEqual(self.state.get_post(review)["state"], "scheduled")
        for state in ("writing", "held", "scheduled", "published"):
            post = self.state.add_post(SLOT, state=state)
            self.assertIn("Không thực hiện được", self.run_command(f"/nailblog approve {post}")[0])
            self.assertEqual(self.state.get_post(post)["state"], state)
        self.assertIn("unknown post", self.run_command("/nailblog approve 999")[0])

    def test_hold_follows_the_lifecycle_graph(self):
        writing = self.state.add_post(SLOT, state="writing")
        self.run_command(f"/nailblog hold {writing} nguồn thiếu")
        row = self.state.get_post(writing)
        self.assertEqual((row["state"], row["reason"]), ("held", "nguồn thiếu"))
        uncertain = self.state.add_post(SLOT, state="uncertain")
        self.run_command(f"/nailblog hold {uncertain} xem lại")
        self.assertEqual(self.state.get_post(uncertain)["state"], "held")
        for state in ("review", "scheduled"):
            post = self.state.add_post(SLOT, state=state)
            self.run_command(f"/nailblog hold {post} giữ slot")
            row = self.state.get_post(post)
            self.assertEqual((row["state"], row["reason"]), ("held", "giữ slot"))
        for state in ("before_publish", "published", "held"):  # no such edge in the graph
            post = self.state.add_post(SLOT, state=state)
            self.assertIn("Không thực hiện được", self.run_command(f"/nailblog hold {post} x")[0])
            self.assertEqual(self.state.get_post(post)["state"], state)

    def test_move_changes_slot_only_for_unpublished_posts_and_future_slots(self):
        post = self.state.add_post(SLOT, state="scheduled")
        self.assertIn("2026-10-20T19:00+07:00", self.run_command(f"/nailblog move {post} 2026-10-20 19")[0])
        self.assertEqual(self.state.get_post(post)["slot_at"], "2026-10-20T19:00+07:00")
        self.assertIn("đã qua", self.run_command(f"/nailblog move {post} 2026-10-17 06", now=NOW + 4 * 3600)[0])
        self.assertEqual(self.state.get_post(post)["slot_at"], "2026-10-20T19:00+07:00")
        self.assertIn("Slot phải", self.run_command(f"/nailblog move {post} 2026-10-21 07")[0])
        for state in ("before_publish", "published", "uncertain"):
            locked = self.state.add_post(SLOT, state=state)
            self.assertIn("Không dời được", self.run_command(f"/nailblog move {locked} 2026-10-20 06")[0])
            self.assertEqual(self.state.get_post(locked)["slot_at"], SLOT)

    def test_queue_lists_today_and_tomorrow_only(self):
        today = self.state.add_post("2026-10-17T06:00+07:00")
        tomorrow = self.state.add_post("2026-10-18T19:00+07:00", state="review")
        later = self.state.add_post("2026-10-19T06:00+07:00")
        text = self.run_command("/nailblog queue")[0]
        self.assertIn(f"#{today} 2026-10-17T06:00+07:00 writing", text)
        self.assertIn(f"#{tomorrow} 2026-10-18T19:00+07:00 review", text)
        self.assertNotIn(f"#{later} ", text)
        self.state.set_paused(False)
        empty = State(Path(self.directory.name) / "empty.sqlite3")
        self.assertEqual(execute(empty, "queue", (), now=NOW, config=self.config), ["Không có bài nào trong 2 ngày tới."])

    def test_status_reports_state_and_todays_cost(self):
        self.state.add_post(SLOT, state="review")
        self.state.add_usage(NOW, "gemini", "img", 1, 0.75)
        self.state.add_usage(NOW - 2 * 86400, "gemini", "img", 1, 9.0)
        self.state.set_paused(True)
        text = self.run_command("/nailblog status")[0]
        for part in ("mode=preview", "paused=True", "ảnh=fixed", "review=1", "Sổ API cũ: uncertain 0.000000 USD",
                     "outbox: 0 tin chờ gửi"):
            self.assertIn(part, text)

    def test_service_command_applies_to_its_own_state(self):
        service = Service(self.config, state=self.state, clock=lambda: NOW)
        service.command("pause")
        self.assertEqual(self.state.control()["paused"], 1)
        service.command("mode", ("adaptive",))
        self.assertEqual(self.state.control()["image_mode"], "adaptive")


class OverrideCommandTests(unittest.IsolatedAsyncioTestCase):
    """`/nailblog ok <id> override <lý do>` end to end on a real sqlite State."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state = State(Path(self.directory.name) / "state.sqlite3")
        from nailblog.approvals import ApprovalStore
        self.store = ApprovalStore(self.state)
        self.held = self.store.request("draft", "run1", "snap", NOW + 3600, NOW, requires_override=True,
                                       issues=[{"code": "cliche", "message": "sáo rỗng", "overridable": True}])

    async def say(self, text, sender=OWNER):
        service = SimpleNamespace(state=self.state, clock=lambda: NOW + 1, command=Mock(return_value=["?"]))
        event = dm(raw_text=text, sender_id=sender, chat_id=sender)
        event.get_sender = AsyncMock(return_value=SimpleNamespace(id=sender, bot=False, is_self=False))
        event.get_chat = AsyncMock(return_value=SimpleNamespace(id=sender, bot=False, is_self=False))
        await route_command(event, service, (sender,))
        return [call.args[0] for call in event.reply.await_args_list]

    def test_parse_override_forms(self):
        self.assertEqual(parse("/nailblog ok 5"), ("ok", (5,)))
        self.assertEqual(parse("/nailblog ok 5 override đã đọc kỹ"), ("ok_override", (5, "đã đọc kỹ")))
        self.assertEqual(parse("/nailblog ok 5 OVERRIDE"), ("ok_override", (5, "")))
        self.assertIsNone(parse("/nailblog ok 5 force vì sao"))
        self.assertIsNone(parse("/nailblog ok 5 6"))

    async def test_plain_ok_then_empty_reason_then_override_with_reason(self):
        reply = (await self.say(f"/nailblog ok {self.held}"))[0]
        self.assertIn("[override_required]", reply)
        self.assertIn(f"/nailblog ok {self.held} override <lý do>", reply)
        self.assertIn("[reason_required]", (await self.say(f"/nailblog ok {self.held} override"))[0])
        self.assertEqual(self.store.get(self.held)["status"], "pending")
        reply = (await self.say(f"/nailblog ok {self.held} override chấp nhận lỗi văn phong"))[0]
        self.assertIn("[recorded]", reply)
        self.assertIn("OK + override", reply)
        self.assertEqual(self.store.get(self.held)["reason"], "chấp nhận lỗi văn phong")
        self.assertIn("[already_decided]", (await self.say(f"/nailblog ok {self.held} override lại"))[0])

    async def test_wrong_sender_expired_and_superseded_have_vietnamese_messages(self):
        service = SimpleNamespace(state=self.state, clock=lambda: NOW + 1)
        from nailblog.commands import decide_approval
        wrong = decide_approval(self.state, "ok", self.held, "x", sender_id=OWNER + 1, owner_ids=(OWNER,), now=NOW + 1,
                                override=True)[0]
        self.assertIn("[wrong_sender]", wrong)
        self.assertIn("không phải Owner", wrong)
        late = decide_approval(self.state, "ok", self.held, "x", sender_id=OWNER, owner_ids=(OWNER,), now=NOW + 9999,
                               override=True)[0]
        self.assertIn("[expired]", late)
        self.assertIn("quá hạn", late)
        newer = self.store.request("draft", "run1", "snap2", NOW + 3600, NOW)
        old = self.store.request("draft", "run9", "a", NOW + 3600, NOW)
        self.store.request("draft", "run9", "b", NOW + 3600, NOW)
        stale = decide_approval(self.state, "ok", old, "", sender_id=OWNER, owner_ids=(OWNER,), now=NOW + 1)[0]
        self.assertIn("[superseded]", stale)
        self.assertIn("thẻ mới", stale)
        self.assertNotEqual(newer, self.held)
        self.assertEqual(self.state.control()["paused"], 0)
        del service

    def test_flow_lists_each_run_by_slot_with_override_flag_and_deadline(self):
        from nailblog.autoflow import AutoflowRuns
        runs = AutoflowRuns(self.state)
        runs.create("rB", 2, "2026-10-17T19:00+07:00", "/o")
        runs.create("rA", 1, "2026-10-17T06:00+07:00", "/o")
        ok_card = self.store.request("images", "rA", "s", NOW + 7200, NOW)
        runs.update("rA", stage="images_wait", ctx={"images": {"id": ok_card, "snapshot": "s"}})
        runs.update("rB", stage="draft_wait", ctx={"draft": {"id": self.held, "snapshot": "snap"}})
        lines = execute(self.state, "flow", (), now=NOW, config=None)[0].split("\n")
        self.assertIn("slot 2026-10-17T06:00+07:00 [images_wait]", lines[1])
        self.assertIn("requires_override=không", lines[1])
        self.assertIn("slot 2026-10-17T19:00+07:00 [draft_wait]", lines[2])
        self.assertIn("requires_override=có", lines[2])
        self.assertIn(f"/nailblog ok {self.held} override <lý do>", lines[2])
        self.assertIn("hạn duyệt", lines[2])


if __name__ == "__main__":
    unittest.main()
