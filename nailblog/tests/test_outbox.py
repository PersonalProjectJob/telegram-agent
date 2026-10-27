"""Durable Owner outbox (S6): fixed random_id across a crash, backoff, Owner check, media partial delivery, and the
20:00 report. Real sqlite State in a temp dir; no network, no Telegram."""
import asyncio
import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

from nailblog import daily
from nailblog.autoflow import AutoflowRuns
from nailblog.notices import (Outbox, OutboxBackoff, backoff_seconds, deliver_pending, message_ids, notice_key,
                              outbox_media_sender, send_once)
from nailblog.state import State
from nailblog.telegram import make_owner_media_sender, make_owner_outbox_sender

OWNER = 111
NOW = 1_000_000.0


class FakeServer:
    """Telegram dedupes a send by random_id; `lose_response` simulates a crash after the server accepted."""

    def __init__(self):
        self.messages = {}
        self.lose_response = 0
        self.calls = []

    def accept(self, random_id, text):
        self.calls.append(random_id)
        ident = self.messages.setdefault(random_id, (len(self.messages) + 100, text))[0]
        if self.lose_response:
            self.lose_response -= 1
            raise ConnectionError('response lost')
        return ident


class Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'state.db'
        self.state = State(self.path)
        self.server = FakeServer()

        async def send(text, random_id=None):
            return self.server.accept(random_id, text)
        self.send = send


class OutboxTests(Base):
    async def test_crash_mid_send_resends_with_the_same_random_id_and_does_not_duplicate(self):
        key = notice_key('held', 'run1')
        self.server.lose_response = 1
        self.assertFalse(await send_once(self.state, self.send, key, 'held card', now=NOW))
        first = Outbox(self.state).get(key)
        self.assertEqual((first['status'], first['attempts']), ('pending', 1))
        restarted = State(self.path)                      # process restart: only the DB survives
        self.assertFalse(await send_once(restarted, self.send, key, 'held card', now=NOW + 10))   # still backing off
        self.assertEqual(len(self.server.calls), 1)
        self.assertTrue(await send_once(restarted, self.send, key, 'held card', now=NOW + 400))
        done = Outbox(restarted).get(key)
        self.assertEqual(self.server.calls, [first['random_id']] * 2)
        self.assertEqual(done['random_id'], first['random_id'])
        self.assertEqual(len(self.server.messages), 1)    # the server saw one message only
        self.assertEqual((done['status'], json.loads(done['message_ids'])), ('sent', [100]))
        self.assertFalse(await send_once(restarted, self.send, key, 'held card', now=NOW + 9999))
        self.assertEqual(len(self.server.calls), 2)

    async def test_failure_keeps_the_queue_and_backs_off_exponentially(self):
        async def broken(text, random_id=None):
            raise ConnectionError('down')
        key = notice_key('x', 1)
        self.assertFalse(await send_once(self.state, broken, key, 'a', now=NOW))
        self.assertEqual([r['key'] for r in Outbox(self.state).pending()], [key])
        self.assertEqual(Outbox(self.state).get(key)['next_attempt_at'], NOW + backoff_seconds(1))
        self.assertFalse(await send_once(self.state, broken, key, 'a', now=NOW + backoff_seconds(1)))
        self.assertEqual(Outbox(self.state).get(key)['next_attempt_at'],
                         NOW + backoff_seconds(1) + backoff_seconds(2))
        self.assertEqual((backoff_seconds(1), backoff_seconds(2), backoff_seconds(99)), (300, 600, 3600))
        self.assertEqual(await deliver_pending(self.state, self.send, now=NOW + 10_000), 1)
        self.assertEqual(Outbox(self.state).pending_count(), 0)

    async def test_legacy_sender_without_random_id_still_works_and_existing_key_keeps_its_text(self):
        seen = []

        async def legacy(text):
            seen.append(text)
        key = notice_key('legacy', 1)
        self.assertTrue(await send_once(self.state, legacy, key, 'one', now=NOW))
        self.assertFalse(await send_once(self.state, legacy, key, 'two', now=NOW + 1))
        self.assertEqual(seen, ['one'])

    async def test_a_notice_delivered_before_the_outbox_existed_is_not_sent_again(self):
        key = notice_key('old', 1)
        self.state.notice_sent(key, NOW)
        self.assertFalse(await send_once(self.state, self.send, key, 'again', now=NOW + 5))
        self.assertEqual(self.server.calls, [])

    def test_message_ids_reads_ids_lists_messages_and_update_shapes(self):
        self.assertEqual(message_ids([SimpleNamespace(id=5), 6, None, True]), [5, 6])
        class UpdateMessageID:
            id, random_id = 9, 5
        self.assertEqual(message_ids(SimpleNamespace(id=7)), [7])
        self.assertEqual(message_ids(SimpleNamespace(updates=[UpdateMessageID()], update=None)), [9])


class SenderTests(Base):
    async def test_owner_binding_is_checked_by_the_senders(self):
        client = SimpleNamespace()
        for ids in ((), (1, 2), (0,), ('1',)):
            with self.assertRaises(ValueError):
                make_owner_outbox_sender(client, ids)
            with self.assertRaises(ValueError):
                make_owner_media_sender(client, ids)
        media = make_owner_media_sender(client, (OWNER,))
        with self.assertRaises(ValueError):
            await media(OWNER + 1, [], 'x', random_id=5)

    async def test_text_sender_passes_the_fixed_random_id_to_the_raw_request(self):
        got = []

        async def raw(client, owner, text, random_id):
            got.append((owner, text, random_id))
            return [42]
        send = make_owner_outbox_sender(SimpleNamespace(), (OWNER,), raw_send=raw)
        self.assertEqual(await send('hi', random_id=77), [42])
        self.assertEqual(got, [(OWNER, 'hi', 77)])

    async def test_media_card_partial_failure_keeps_the_album_ids_and_does_not_resend_the_album(self):
        class Client:
            def __init__(self):
                self.albums, self.captions = 0, []
                self.fail_caption = True

            async def send_file(self, owner, streams):
                self.albums += 1
                return [SimpleNamespace(id=11), SimpleNamespace(id=12)]

        client = Client()

        async def raw(c, owner, text, random_id):
            client.captions.append(random_id)
            if client.fail_caption:
                client.fail_caption = False
                raise ConnectionError('boom')
            return [13]
        clock = [NOW]
        sender = outbox_media_sender(self.state, make_owner_media_sender(client, (OWNER,), raw_send=raw),
                                     clock=lambda: clock[0])
        with self.assertRaises(Exception):
            await sender(OWNER, [b'jpeg1', b'jpeg2'], 'card')
        with self.assertRaises(OutboxBackoff):
            await sender(OWNER, [b'jpeg1', b'jpeg2'], 'card')
        clock[0] = NOW + 400
        self.assertEqual(await sender(OWNER, [b'jpeg1', b'jpeg2'], 'card'), [11, 12, 13])
        self.assertEqual(client.albums, 1)
        self.assertEqual(len(set(client.captions)), 1)            # same random_id on both caption attempts
        self.assertEqual(await sender(OWNER, [b'jpeg1', b'jpeg2'], 'card'), [11, 12, 13])
        self.assertEqual((client.albums, len(client.captions)), (1, 2))


class CardBackoffTests(Base):
    async def test_cards_retry_every_300s_while_other_notices_back_off_to_3600s(self):
        class Client:
            async def send_file(self, owner, streams):
                raise ConnectionError('down')
        clock = [NOW]
        sender = outbox_media_sender(self.state, make_owner_media_sender(Client(), (OWNER,)), clock=lambda: clock[0])
        key = None
        for attempt in range(1, 6):
            with self.assertRaises(Exception):
                await sender(OWNER, [b'jpeg'], 'card')
            row = Outbox(self.state).pending()[0]
            key = row['key']
            self.assertEqual(row['next_attempt_at'], clock[0] + 300)       # never grows past 300 for a card
            clock[0] = row['next_attempt_at']
        self.assertEqual(Outbox(self.state).get(key)['attempts'], 5)

        async def broken(text, random_id=None):
            raise ConnectionError('down')
        text_key = notice_key('t', 1)
        now, deltas = NOW, []
        for _ in range(6):
            await send_once(self.state, broken, text_key, 'x', now=now)
            due = Outbox(self.state).get(text_key)['next_attempt_at']
            deltas.append(due - now)
            now = due
        self.assertEqual(deltas, [300, 600, 1200, 2400, 3600, 3600])


class ReportTests(Base):
    DAY = date(2026, 10, 27)

    def service(self, **extra):
        config = SimpleNamespace(slots=('06:00', '10:00', '15:00', '19:00'), mode='live',
                                 daily=SimpleNamespace(preview_at='20:00'), autoflow=SimpleNamespace(enabled=True),
                                 contract=SimpleNamespace(site_origin='https://staging.example'),
                                 subscription=SimpleNamespace(max_daily_jobs=48),
                                 budget=SimpleNamespace(ledger_db_path=''),
                                 schedule=SimpleNamespace(window_start_hours=6))
        return SimpleNamespace(state=self.state, config=config, clock=lambda: NOW, **extra)

    def topic(self, hhmm, title):
        slot = daily.slot_text(self.DAY, hhmm)
        topic_id = self.state.add_topic(daily.target_week(self.DAY), title, 'hot-trend', slot_at=slot, state='approved')
        return topic_id, slot

    async def test_report_has_every_slot_and_says_chua_co_instead_of_inventing(self):
        pub_id, pub_slot = self.topic('06:00', 'Bài đã đăng')
        post = self.state.add_post(pub_slot, topic_id=pub_id, state='writing', draft={'article': {'title': 'Tiêu đề thật'}})
        evidence = {'readback': {'slug': 'tieu-de-that', 'publishedAt': '2026-10-26T23:00:00Z',
                                 'image_checks': [{'mime_defect': False}, {'mime_defect': True}]}}
        with self.state.connection() as db:
            db.execute("UPDATE posts SET state='published', admin_id='77', details=? WHERE id=?",
                       (json.dumps({'evidence': evidence}), post))
        held_id, held_slot = self.topic('10:00', 'Bài bị giữ')
        runs = AutoflowRuns(self.state)
        runs.create('r2', held_id, held_slot, '/tmp/x')
        runs.update('r2', stage='held', terminal_reason='text_quality_held')
        self.topic('15:00', 'Chưa có bài')                    # chosen topic, no post, no run
        service = self.service(agy_jobs_today=lambda: 7)
        text, complete = await daily.preview_dm(service, None, self.DAY)
        self.assertTrue(complete)
        for part in ('Slot 06:00', 'Slot 10:00', 'Slot 15:00', 'Slot 19:00', 'Tiêu đề thật',
                     'https://staging.example/blog/tieu-de-that', 'Draft: đã lưu (admin_id 77)',
                     'Publish: 06:00', 'Readback: có', 'mime_defect: CÓ', 'Held: text_quality_held',
                     'Job agy hôm nay: 7 / 48', 'Sổ API cũ: uncertain 0.000000 USD'):
            self.assertIn(part, text)
        self.assertIn('Slot 19:00 ngày 27/10: chưa có chủ đề.', text)
        third = text.split('Slot 15:00')[1].split('Slot 19:00')[0]
        self.assertIn('URL: chưa có', third)
        self.assertIn('Readback: chưa có', third)
        self.assertIn('mime_defect: chưa có', third)
        self.assertIn('Draft: chưa có', third)

    async def test_broken_ledger_or_job_counter_does_not_break_the_dm(self):
        from unittest.mock import patch

        def boom():
            raise RuntimeError('counter down')
        with patch('nailblog.paid.report', side_effect=RuntimeError('ledger down')):
            text, complete = await daily.preview_dm(self.service(agy_jobs_today=boom), None, self.DAY)
        self.assertTrue(complete)
        self.assertIn('Job agy hôm nay: chưa có / 48', text)
        self.assertIn('Sổ API cũ: chưa có', text)
        self.assertIn('Slot 06:00', text)

    async def test_agy_job_count_is_chua_co_without_a_counter(self):
        text, _ = await daily.preview_dm(self.service(), None, self.DAY)
        self.assertIn('Job agy hôm nay: chưa có / 48', text)


if __name__ == '__main__':
    unittest.main()
