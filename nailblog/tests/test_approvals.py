import asyncio
import hashlib
import io
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from PIL import Image

from nailblog.approvals import ApprovalError, ApprovalStore, build_preview
from nailblog.state import State
from nailblog.telegram import OwnerMediaSendError, make_owner_media_sender

OWNER = 111
NOW = 1_000_000.0


def webp(path, color):
    Image.new('RGB', (64, 36), color).save(path, 'WEBP')
    return path


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = State(self.root / 'state.db')
        self.store = ApprovalStore(self.state)


class DecisionTests(Base):
    def new(self, snapshot='s1', kind='images', deadline=NOW + 100):
        return self.store.request(kind, 'run1', snapshot, deadline, NOW)

    def test_table_idempotent_and_request_idempotent(self):
        ApprovalStore(self.state)
        first = self.new()
        self.assertEqual(self.new(), first)
        self.assertEqual(len(self.store.pending()), 1)

    def test_new_snapshot_supersedes_old_pending_only_same_kind(self):
        old = self.new('s1')
        draft = self.new('d1', kind='draft')
        new = self.new('s2')
        self.assertNotEqual(old, new)
        self.assertEqual(self.store.get(old)['status'], 'superseded')
        self.assertEqual(self.store.get(new)['status'], 'pending')
        self.assertEqual(self.store.get(draft)['status'], 'pending')
        self.assertEqual([r['id'] for r in self.store.pending('images')], [new])
        refused = self.store.decide(old, 'ok', None, OWNER, OWNER, NOW + 1)
        self.assertEqual((refused['ok'], refused['code']), (False, 'superseded'))
        self.assertEqual(self.store.get(old)['status'], 'superseded')

    def test_ok_and_repeat_is_noop(self):
        rid = self.new()
        first = self.store.decide(rid, 'ok', None, OWNER, OWNER, NOW + 1)
        self.assertEqual((first['ok'], first['code'], first['status']), (True, 'recorded', 'approved'))
        before = self.store.get(rid)
        again = self.store.decide(rid, 'ok', None, OWNER, OWNER, NOW + 50)
        self.assertEqual((again['ok'], again['status'], again['id']), (True, 'approved', rid))
        self.assertEqual(self.store.get(rid), before)

    def test_repeat_allowed_even_after_deadline_but_flip_refused(self):
        rid = self.new()
        self.store.decide(rid, 'ok', None, OWNER, OWNER, NOW + 1)
        late = self.store.decide(rid, 'ok', None, OWNER, OWNER, NOW + 1000)
        self.assertTrue(late['ok'])
        flip = self.store.decide(rid, 'reject', 'đổi ý', OWNER, OWNER, NOW + 2)
        self.assertEqual((flip['ok'], flip['code']), (False, 'already_decided_differently'))
        self.assertEqual(self.store.get(rid)['status'], 'approved')

    def test_reject_records_reason_and_repeat_noop(self):
        rid = self.new()
        done = self.store.decide(rid, 'reject', '  ảnh   xấu ', OWNER, OWNER, NOW + 1)
        self.assertEqual((done['ok'], done['status'], done['reason']), (True, 'rejected', 'ảnh xấu'))
        row = self.store.get(rid)
        self.assertEqual((row['decided_by'], row['decided_at']), (OWNER, NOW + 1))
        again = self.store.decide(rid, 'reject', 'lý do khác', OWNER, OWNER, NOW + 2)
        self.assertTrue(again['ok'])
        self.assertEqual(self.store.get(rid), row)
        flip = self.store.decide(rid, 'ok', None, OWNER, OWNER, NOW + 3)
        self.assertEqual(flip['code'], 'already_decided_differently')

    def test_empty_reject_reason_refused_without_state_change(self):
        rid = self.new()
        for reason in (None, '', '   \n'):
            result = self.store.decide(rid, 'reject', reason, OWNER, OWNER, NOW + 1)
            self.assertEqual((result['ok'], result['code']), (False, 'reason_required'))
        self.assertEqual(self.store.get(rid)['status'], 'pending')

    def test_wrong_sender_refused_without_state_change(self):
        rid = self.new()
        for sender in (222, '111', True, None):
            result = self.store.decide(rid, 'ok', None, sender, OWNER, NOW + 1)
            self.assertEqual((result['ok'], result['code']), (False, 'wrong_sender'))
        self.assertEqual(self.store.get(rid)['status'], 'pending')
        self.assertIsNone(self.store.get(rid)['decided_by'])

    def test_wrong_sender_cannot_even_expire_or_probe(self):
        rid = self.new()
        result = self.store.decide(rid, 'ok', None, 222, OWNER, NOW + 10_000)
        self.assertEqual(result['code'], 'wrong_sender')
        self.assertIsNone(result['status'])
        self.assertEqual(self.store.get(rid)['status'], 'pending')

    def test_expiry_marks_expired_and_stays_refused(self):
        rid = self.new(deadline=NOW + 10)
        edge = self.store.decide(rid, 'ok', None, OWNER, OWNER, NOW + 10)
        self.assertTrue(edge['ok'])
        rid2 = self.new('s2', deadline=NOW + 10)
        late = self.store.decide(rid2, 'ok', None, OWNER, OWNER, NOW + 11)
        self.assertEqual((late['ok'], late['code'], late['status']), (False, 'expired', 'expired'))
        self.assertEqual(self.store.get(rid2)['status'], 'expired')
        again = self.store.decide(rid2, 'ok', None, OWNER, OWNER, NOW)
        self.assertEqual((again['ok'], again['code']), (False, 'expired'))
        self.assertEqual(self.store.pending(), [])

    def test_unknown_id_and_invalid_verdict(self):
        self.assertEqual(self.store.decide(99, 'ok', None, OWNER, OWNER, NOW)['code'], 'unknown_request')
        with self.assertRaises(ValueError):
            self.store.decide(1, 'maybe', None, OWNER, OWNER, NOW)

    def test_schema_constraints_and_message_ids(self):
        rid = self.new()
        self.store.record_messages(rid, [5, 6])
        self.assertEqual(self.store.get(rid)['message_ids'], '[5, 6]')
        with self.state.connection() as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO approval_requests(run_id,kind,snapshot,status,created_at,deadline) "
                           "VALUES('r','other','s','pending',1,1)")
        with self.assertRaises(ValueError):
            self.store.request('other', 'run1', 's', NOW, NOW)

    def test_sender_and_owner_must_be_matching_positive_ints(self):
        rid = self.new()
        cases = [(None, None, 'owner_unbound'), (True, 1, 'wrong_sender'), (0, 0, 'owner_unbound'),
                 ('1', '1', 'owner_unbound'), (1, '1', 'owner_unbound'), (-5, -5, 'owner_unbound'),
                 (True, True, 'owner_unbound'), (1.0, 1, 'wrong_sender'), ('1', 1, 'wrong_sender'),
                 (None, 1, 'wrong_sender'), (2, 1, 'wrong_sender')]
        for sender, owner, code in cases:
            for verdict in ('ok', 'reject'):
                result = self.store.decide(rid, verdict, 'lý do', sender, owner, NOW + 1)
                self.assertEqual((result['ok'], result['code']), (False, code), (sender, owner))
        row = self.store.get(rid)
        self.assertEqual((row['status'], row['decided_by'], row['decided_at']), ('pending', None, None))

    def test_concurrent_opposite_decisions_have_exactly_one_winner(self):
        path = self.root / 'state.db'
        stores = [ApprovalStore(State(path)), ApprovalStore(State(path))]
        for index in range(50):
            rid = stores[0].request('images', 'run-c', f'snap{index}', NOW + 100, NOW)
            barrier = threading.Barrier(2)
            results = {}

            def work(slot, verdict):
                barrier.wait()
                try:
                    results[slot] = stores[slot].decide(rid, verdict, 'x', OWNER, OWNER, NOW + 1)
                except Exception as error:  # a lock error must fail the assertions below
                    results[slot] = {'ok': 'error', 'code': type(error).__name__}

            threads = [threading.Thread(target=work, args=(0, 'ok')), threading.Thread(target=work, args=(1, 'reject'))]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            winners = [slot for slot in (0, 1) if results[slot]['ok'] is True]
            losers = [slot for slot in (0, 1) if slot not in winners]
            self.assertEqual(len(winners), 1, results)
            self.assertEqual(results[losers[0]]['code'], 'already_decided_differently', results)
            expected = 'approved' if winners[0] == 0 else 'rejected'
            row = stores[0].get(rid)
            self.assertEqual(row['status'], expected)
            self.assertEqual(row['reason'], 'x' if expected == 'rejected' else None)

    def test_request_for_superseded_snapshot_is_refused_not_dead_id(self):
        first = self.new('s1')
        second = self.new('s2')
        with self.assertRaises(ApprovalError) as raised:
            self.new('s1')
        self.assertEqual(str(raised.exception), 'snapshot_superseded')
        self.assertEqual(self.new('s2'), second)
        self.assertEqual(self.store.get(first)['status'], 'superseded')
        self.assertEqual(self.store.get(second)['status'], 'pending')

    def test_decided_snapshot_is_never_revived(self):
        rid = self.new('s1')
        self.store.decide(rid, 'reject', 'không đạt', OWNER, OWNER, NOW + 1)
        self.assertEqual(self.new('s1'), rid)
        self.assertEqual(self.store.get(rid)['status'], 'rejected')
        self.assertEqual(self.store.pending(), [])

    def test_is_approved_exact_snapshot_only(self):
        rid = self.new('s1')
        self.assertFalse(self.store.is_approved('run1', 'images', 's1'))
        self.store.decide(rid, 'ok', None, OWNER, OWNER, NOW + 1)
        self.assertTrue(self.store.is_approved('run1', 'images', 's1'))
        for args in (('run1', 'images', 's2'), ('run1', 'draft', 's1'), ('run2', 'images', 's1'),
                     ('run1', 'images', ''), (None, 'images', 's1'), ('run1', 'images', None)):
            self.assertFalse(self.store.is_approved(*args), args)
        rid2 = self.new('s2')
        self.store.decide(rid2, 'reject', 'no', OWNER, OWNER, NOW + 1)
        self.assertFalse(self.store.is_approved('run1', 'images', 's2'))

    def test_survives_reopen(self):
        rid = self.new()
        reopened = ApprovalStore(State(self.root / 'state.db'))
        self.assertEqual(reopened.get(rid)['status'], 'pending')


class FakeClient:
    def __init__(self):
        self.calls = []

    async def send_file(self, entity, files, **kwargs):
        self.calls.append(('file', entity, [f.read() for f in files], [f.name for f in files], kwargs))
        return [type('M', (), {'id': 100 + i})() for i, _ in enumerate(files)]

    async def send_message(self, entity, text, **kwargs):
        self.calls.append(('message', entity, text, kwargs))
        return type('M', (), {'id': 200})()


def artifact(**extra):
    base = {'snapshot': 'abcdef0123456789' * 4, 'article': {'title': 'Mẫu móng <b>đẹp</b>', 'summary': 'Tóm tắt ngắn'},
            'words': 1234, 'quality': {'verdict': 'pass', 'issues': []}, 'facts': {'ungrounded': [{'n': 1}, {'n': 2}]},
            'image_quota_forecast': {'usd': 0.4}, 'slot_at': '2026-10-24T20:00:00+07:00',
            'approval_id': 7, 'approval_deadline': NOW}
    base.update(extra)
    return base


class PreviewTests(Base):
    def images(self):
        return [webp(self.root / f'img{i}.webp', (i * 40, 90, 200)) for i in range(3)]

    def test_images_preview_real_jpegs_and_sources_untouched(self):
        paths = self.images()
        before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]
        jpegs, card, page = build_preview('images', artifact(review_bypass={'enabled': True}), paths)
        self.assertEqual(len(jpegs), 3)
        for data in jpegs:
            with Image.open(io.BytesIO(data)) as picture:
                self.assertEqual((picture.format, picture.size), ('JPEG', (64, 36)))
        self.assertEqual(before, [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths])
        self.assertEqual(sorted(p.name for p in self.root.glob('img*')), [f'img{i}.webp' for i in range(3)])
        self.assertEqual(page.count('data:image/jpeg;base64,'), 3)
        self.assertNotIn('<b>', page)
        self.assertIn('&lt;b&gt;', page)

    def test_images_card_fields_and_commands(self):
        _, card, _ = build_preview('images', artifact(review_bypass={'enabled': True}), self.images())
        for part in ('Mẫu móng', 'Tóm tắt ngắn', '1234', 'pass', 'Dữ kiện chưa có nguồn: 2',
                     'ảnh chưa qua model chấm (bypass)', '0.40 USD', '2026-10-24T20:00:00+07:00',
                     'abcdef012345', 'Hạn duyệt: 1970-01-12'):
            self.assertIn(part, card)
        self.assertNotIn('abcdef0123456', card)
        self.assertTrue(card.endswith('/nailblog ok 7\n/nailblog reject 7 <lý do>'))

    def test_bypass_line_only_when_bypass_on(self):
        _, off, _ = build_preview('images', artifact(), self.images())
        _, off2, _ = build_preview('images', artifact(review_bypass={'enabled': False}), self.images())
        _, on, _ = build_preview('images', artifact(review_bypass={'enabled': True}), self.images())
        self.assertNotIn('bypass', off)
        self.assertNotIn('bypass', off2)
        self.assertIn('ảnh chưa qua model chấm (bypass)', on)

    def test_missing_optional_fields_render_dash(self):
        jpegs, card, page = build_preview('images', {}, [])
        self.assertEqual(jpegs, [])
        self.assertGreaterEqual(card.count('—'), 7)
        self.assertTrue(card.endswith('/nailblog ok <id>\n/nailblog reject <id> <lý do>'))
        _, draft, _ = build_preview('draft', {'article': 5, 'facts': 'x', 'quality': None}, [])
        self.assertIn('admin_id: —', draft)
        build_preview('images', {'facts': {'ungrounded': None}, 'image_quota_forecast': 'x', 'words': True}, [])

    def test_neurons_forecast_is_labelled_not_usd(self):
        _, card, _ = build_preview('images', artifact(image_quota_forecast={'unit': 'neurons',
                                                                          'run_charged_or_estimated': 12.5}), [])
        self.assertIn('12.5 neurons (chưa quy USD)', card)

    def test_draft_card(self):
        jpegs, card, page = build_preview('draft', artifact(admin_id=987, draft_fingerprint='f' * 64), self.images())
        self.assertEqual(jpegs, [])
        self.assertIn('admin_id: 987', card)
        self.assertIn('Fingerprint Draft: ' + 'f' * 12 + '\n', card)
        self.assertNotIn('f' * 13, card)
        self.assertNotIn('data:image', page)
        self.assertTrue(card.endswith('/nailblog ok 7\n/nailblog reject 7 <lý do>'))

    def test_explicit_id_and_deadline_override(self):
        _, card, _ = build_preview('draft', artifact(), [], approval_id=42, deadline=NOW + 3600)
        self.assertIn('/nailblog ok 42', card)
        self.assertIn('Hạn duyệt: 1970-01-12 21:46', card)

    def test_no_secrets_in_cards(self):
        secret = {'api_key': 'KEY-SECRET', 'cookie': 'COOKIE-SECRET', 'token': 'TOK-SECRET',
                  'url': 'https://x.test/?token=TOK-SECRET', 'environment': {'key_file': '/etc/key'}}
        for kind in ('images', 'draft'):
            jpegs, card, page = build_preview(kind, artifact(admin_id=1, draft_fingerprint='a' * 64,
                                                              review_bypass={'enabled': True}, **secret), self.images())
            for text in (card, page):
                for needle in ('SECRET', 'TOK-', 'https://', 'http://', 'key_file', '/etc/key'):
                    self.assertNotIn(needle, text)

    def test_card_capped_at_telegram_limit_commands_intact(self):
        huge = 'x' * 10_000
        for kind in ('images', 'draft'):
            _, card, _ = build_preview(kind, artifact(article={'title': huge, 'summary': huge}, admin_id=huge,
                                                      draft_fingerprint=huge, review_bypass={'enabled': True}), [])
            self.assertLessEqual(len(card), 4096)
            self.assertTrue(card.endswith('/nailblog ok 7\n/nailblog reject 7 <lý do>'))
            self.assertIn('…', card)
        _, card, _ = build_preview('images', artifact(article={'title': huge, 'summary': huge}), [])
        self.assertIn('Snapshot: abcdef012345', card)
        self.assertNotIn('x' * 501, card)

    def test_unreadable_image_raises_instead_of_sending_garbage(self):
        bad = self.root / 'bad.webp'
        bad.write_bytes(b'not an image')
        with self.assertRaises(Exception):
            build_preview('images', artifact(), [bad])


class SenderTests(unittest.TestCase):
    def run_send(self, client, owner_ids, owner, files, text='card'):
        return asyncio.run(make_owner_media_sender(client, owner_ids)(owner, files, text))

    def jpeg(self, color):
        out = io.BytesIO()
        Image.new('RGB', (8, 8), color).save(out, 'JPEG')
        return out.getvalue()

    def test_album_then_card_returns_ids(self):
        client = FakeClient()
        files = [self.jpeg('red'), self.jpeg('blue')]
        ids = self.run_send(client, [OWNER], OWNER, files, 'thẻ chữ')
        self.assertEqual(ids, [100, 101, 200])
        kind, entity, streams, names, _ = client.calls[0]
        self.assertEqual((kind, entity, names), ('file', OWNER, ['nailblog-1.jpg', 'nailblog-2.jpg']))
        for data, source in zip(streams, files):
            self.assertEqual(data, source)
            with Image.open(io.BytesIO(data)) as picture:
                self.assertEqual(picture.format, 'JPEG')
        self.assertEqual(client.calls[1][:3], ('message', OWNER, 'thẻ chữ'))
        self.assertEqual(client.calls[1][3], {'parse_mode': None, 'link_preview': False})

    def test_large_album_is_chunked_by_ten(self):
        client = FakeClient()
        ids = self.run_send(client, [OWNER], OWNER, [self.jpeg('red')] * 12)
        self.assertEqual([len(c[2]) for c in client.calls if c[0] == 'file'], [10, 2])
        self.assertEqual(len(ids), 13)

    def test_no_files_sends_only_card(self):
        client = FakeClient()
        self.assertEqual(self.run_send(client, [OWNER], OWNER, []), [200])
        self.assertEqual([c[0] for c in client.calls], ['message'])

    def test_card_failure_carries_album_ids(self):
        class Failing(FakeClient):
            async def send_message(self, entity, text, **kwargs):
                raise ConnectionError('secret-token-in-message')

        with self.assertRaises(OwnerMediaSendError) as raised:
            self.run_send(Failing(), [OWNER], OWNER, [self.jpeg('red'), self.jpeg('blue')])
        self.assertEqual(raised.exception.sent_ids, [100, 101])
        self.assertNotIn('secret-token', str(raised.exception))
        self.assertIn('ConnectionError', str(raised.exception))

    def test_second_chunk_failure_carries_first_chunk_ids(self):
        class Failing(FakeClient):
            async def send_file(self, entity, files, **kwargs):
                if self.calls:
                    raise OSError('boom')
                return await super().send_file(entity, files, **kwargs)

        with self.assertRaises(OwnerMediaSendError) as raised:
            self.run_send(Failing(), [OWNER], OWNER, [self.jpeg('red')] * 11)
        self.assertEqual(raised.exception.sent_ids, list(range(100, 110)))

    def test_owner_checks_match_notice_sender(self):
        client = FakeClient()
        for bad in ([], [1, 2], ['1'], [0], [True]):
            with self.assertRaises(ValueError):
                make_owner_media_sender(client, bad)
        with self.assertRaises(ValueError):
            self.run_send(client, [OWNER], 222, [b'x'])
        self.assertEqual(client.calls, [])


ISSUES = [{'code': 'cliche', 'message': 'Dùng cụm sáo rỗng'}, 'wall_of_text']


def json_issues(row):
    import json
    return json.loads(row['issues'])


class OverrideTests(Base):
    """Decisions on a real sqlite file: repeat, wrong sender, expiry, superseded, override."""

    def held(self, snapshot='h1', deadline=NOW + 100, run='runH'):
        return self.store.request('draft', run, snapshot, deadline, NOW, requires_override=True, issues=ISSUES)

    def decide(self, rid, verdict='ok', reason='', sender=OWNER, now=NOW + 1, **kw):
        return self.store.decide(rid, verdict, reason, sender, OWNER, now, **kw)

    def test_plain_ok_on_a_held_request_needs_override_and_changes_nothing(self):
        rid = self.held()
        result = self.decide(rid)
        self.assertEqual((result['ok'], result['code']), (False, 'override_required'))
        row = self.store.get(rid)
        self.assertEqual((row['status'], row['requires_override'], row['override']), ('pending', 1, 0))
        self.assertEqual(json_issues(row), [{'code': 'cliche', 'message': 'Dùng cụm sáo rỗng'},
                                            {'code': 'wall_of_text', 'message': ''}])
        self.assertFalse(self.store.is_approved('runH', 'draft', 'h1'))

    def test_override_without_reason_is_refused_and_with_reason_approves_and_records_it(self):
        rid = self.held()
        self.assertEqual(self.decide(rid, override=True, reason='  ')['code'], 'reason_required')
        self.assertEqual(self.store.get(rid)['status'], 'pending')
        result = self.decide(rid, override=True, reason='Owner  đã đọc,  chấp nhận')
        self.assertEqual((result['ok'], result['code'], result['status']), (True, 'recorded', 'approved'))
        row = self.store.get(rid)
        self.assertEqual((row['override'], row['reason']), (1, 'Owner đã đọc, chấp nhận'))
        self.assertTrue(self.store.is_approved('runH', 'draft', 'h1'))
        again = self.decide(rid, override=True, reason='lần hai')
        self.assertEqual((again['ok'], again['code']), (True, 'already_decided'))
        self.assertEqual(self.store.get(rid)['reason'], 'Owner đã đọc, chấp nhận')

    def test_reject_still_works_without_override_on_a_held_request(self):
        rid = self.held()
        self.assertEqual(self.decide(rid, 'reject')['code'], 'reason_required')
        self.assertEqual(self.decide(rid, 'reject', 'sai')['status'], 'rejected')

    def test_override_on_a_normal_request_is_accepted_like_ok_and_flagged(self):
        rid = self.store.request('images', 'run1', 's1', NOW + 100, NOW)
        self.assertEqual(self.store.get(rid)['requires_override'], 0)
        result = self.decide(rid, override=True)
        self.assertEqual((result['ok'], result['code'], result['status']), (True, 'recorded', 'approved'))
        self.assertEqual(self.store.get(rid)['override'], 1)
        plain = self.store.request('images', 'run2', 's1', NOW + 100, NOW)
        self.decide(plain)
        self.assertEqual(self.store.get(plain)['override'], 0)

    def test_wrong_sender_expired_and_superseded_refuse_even_with_override(self):
        rid = self.held()
        wrong = self.decide(rid, override=True, reason='x', sender=OWNER + 1)
        self.assertEqual((wrong['ok'], wrong['code']), (False, 'wrong_sender'))
        self.assertEqual(self.store.get(rid)['status'], 'pending')
        late = self.decide(rid, override=True, reason='x', now=NOW + 101)
        self.assertEqual((late['ok'], late['code']), (False, 'expired'))
        self.assertEqual(self.store.get(rid)['status'], 'expired')
        old = self.held('h2', run='runS')
        self.held('h3', run='runS')
        self.assertEqual(self.store.get(old)['status'], 'superseded')
        stale = self.decide(old, override=True, reason='x')
        self.assertEqual((stale['ok'], stale['code']), (False, 'superseded'))
        self.assertFalse(self.store.is_approved('runS', 'draft', 'h2'))

    def test_request_is_idempotent_and_keeps_its_flag(self):
        first = self.held()
        self.assertEqual(self.store.request('draft', 'runH', 'h1', NOW + 100, NOW), first)
        self.assertEqual(self.store.get(first)['requires_override'], 1)

    def test_old_database_without_the_new_columns_is_migrated_and_stays_readable(self):
        path = self.root / 'old.db'
        db = sqlite3.connect(path)
        db.execute("""CREATE TABLE approval_requests(
            id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('images','draft')), snapshot TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('pending','approved','rejected','expired','superseded')),
            reason TEXT, created_at REAL NOT NULL, deadline REAL NOT NULL, decided_at REAL, decided_by INTEGER,
            message_ids TEXT NOT NULL DEFAULT '[]', UNIQUE(run_id, kind, snapshot))""")
        db.execute("INSERT INTO approval_requests(run_id,kind,snapshot,status,created_at,deadline) "
                   "VALUES('old','images','s','pending',1,?)", (NOW + 100,))
        db.commit()
        db.close()
        store = ApprovalStore(State(path))
        ApprovalStore(State(path))   # second open: guard, no duplicate-column error
        row = store.get(1)
        self.assertEqual((row['requires_override'], row['issues'], row['override']), (0, '[]', 0))
        self.assertEqual(store.decide(1, 'ok', '', OWNER, OWNER, NOW + 1)['status'], 'approved')

    def test_held_card_lists_issues_and_all_three_command_lines(self):
        rid = self.held()
        artifact = {'article': {'title': 'T'}, 'snapshot': 'h1' * 8}
        _, card, _ = build_preview('draft', artifact, [], approval_id=rid, deadline=NOW + 100,
                                   requires_override=True, issues=ISSUES)
        for line in (f'/nailblog ok {rid}\n', f'/nailblog reject {rid} <lý do>',
                     'Lỗi kiểm chất lượng (2):', '- cliche: Dùng cụm sáo rỗng', '- wall_of_text',
                     f'Bài trượt kiểm chất lượng — cần /nailblog ok {rid} override <lý do>'):
            self.assertIn(line, card)
        _, normal, _ = build_preview('draft', artifact, [], approval_id=rid, deadline=NOW + 100)
        self.assertNotIn('override', normal)


if __name__ == '__main__':
    unittest.main()
