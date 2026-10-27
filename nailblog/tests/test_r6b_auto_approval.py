"""R6b (D-034): `autoflow.owner_approval = false`, the machine approves images + Draft itself.

Real: sqlite State, ApprovalStore, OnePost, the quality gate, AdminStore, Service.publish_pass, the engine and the
/nailblog router. Fakes: Agy `ask`, image pipeline, Admin browser, Telegram client, frozen clock (all from
test_autoflow_e2e). No network.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nailblog.approvals import ApprovalError, ApprovalStore
from nailblog.config import (STAGING_API_ORIGIN, STAGING_MEDIA_ORIGIN, STAGING_SITE_ORIGIN, AutoflowConfig,
                             ContractConfig, LinksConfig)
from nailblog.onepost import OnePost
from nailblog.publication import AdminError
from tests.test_autoflow_e2e import SLOTS, Base, slot_ts
from tests.test_config_autoflow import LIVE, ConfigCase, live_sections

REASON = 'owner_approval=false (D-034)'
PASS = {'verdict': 'pass', 'issues': []}
AUTO_LINE = 'duyệt: tự động'


def staging(runs_dir, *, owner_approval=False, environment='staging'):
    contract = (ContractConfig(api_origin=STAGING_API_ORIGIN, site_origin=STAGING_SITE_ORIGIN, verified=True,
                               environment='staging', media_origin=STAGING_MEDIA_ORIGIN)
                if environment == 'staging' else ContractConfig(verified=True))
    site = STAGING_SITE_ORIGIN + '/' if environment == 'staging' else None
    links = LinksConfig(site, site, site) if site else LinksConfig()
    return {'contract': contract, 'links': links,
            'autoflow': AutoflowConfig(enabled=True, runs_dir=runs_dir, owner_approval=owner_approval)}


class AutoBase(Base):
    def auto(self, *, slots=SLOTS, owner_approval=False, environment='staging'):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        h = self.harness(slots=slots, overrides=staging(str(Path(temp.name) / 'runs'), owner_approval=owner_approval,
                                                         environment=environment))
        if environment == 'staging':
            h.pipeline.cover = (1376, 768)
        return h

    @staticmethod
    def approval_rows(h):
        with h.state.connection() as db:
            return [dict(r) for r in db.execute('SELECT * FROM approval_requests ORDER BY id')]

    @staticmethod
    def dms(h, needle):
        return [t for t in h.client.texts if needle in t]


class AutoE2ETests(AutoBase):
    async def test_four_slots_publish_with_two_auto_approvals_each_and_no_owner_card(self):
        h = self.auto()
        await h.until_stage('scheduled')
        self.assertEqual((h.counts(), sorted(p['state'] for p in h.posts())), ((4, 0), ['scheduled'] * 4))
        for done, hhmm in enumerate(SLOTS, start=1):
            h.now = slot_ts(hhmm) + 60
            await h.tick(0)
            await h.tick(60)
            self.assertEqual(h.counts(), (4, done), hhmm)
        for _ in range(3):
            await h.tick(300)
        self.assertEqual((h.stages(), [p['state'] for p in h.posts()]), (['published'] * 4, ['published'] * 4))
        self.assertEqual(h.counts(), (4, 4))                       # exactly one Create and one PATCH per post
        self.assertEqual(h.client.cards, [])                       # no card to the Owner
        self.assertEqual(len(self.dms(h, 'đã lên lịch')), 4)       # one scheduled DM per post
        rows = self.approval_rows(h)
        self.assertEqual(len(rows), 8)
        for run in h.rows():
            mine = [r for r in rows if r['run_id'] == run['run_id']]
            self.assertEqual(sorted(r['kind'] for r in mine), ['draft', 'images'])
            for r in mine:
                self.assertEqual((r['status'], r['decided_by'], r['reason'], r['override'], r['requires_override']),
                                 ('approved', 'auto', REASON, 0, 0))
            ctx = json.loads(run['ctx'])
            self.assertEqual({r['kind']: r['snapshot'] for r in mine},
                             {'images': ctx['images']['snapshot'], 'draft': ctx['draft']['snapshot']})
        self.assertEqual(len(h.pipeline.runs), 4)
        self.assertEqual(len(h.ask.calls), 4)

    async def test_flow_shows_automatic_approval_for_every_run(self):
        h = self.auto(slots=('06:00', '10:00'))
        await h.until_stage('scheduled')
        [text] = await h.owner('/nailblog flow')
        self.assertEqual(text.count(AUTO_LINE), 2)
        self.assertNotIn('/nailblog ok', text)

    async def test_owner_approval_true_keeps_the_owner_cards(self):
        h = self.auto(slots=('06:00',), owner_approval=True)
        await h.until_stage('images_wait')
        self.assertEqual(len(h.cards('images')), 1)
        await h.ok_all('images')
        await h.until_stage('draft_wait')
        await h.ok_all('draft')
        await h.until_stage('scheduled')
        self.assertTrue(all(r['decided_by'] != 'auto' for r in self.approval_rows(h)))
        self.assertEqual(self.dms(h, 'tự duyệt'), [])

    async def test_production_environment_never_self_approves(self):
        h = self.auto(slots=('06:00',), environment='production')
        for _ in range(6):
            await h.tick(60)
        self.assertEqual((h.rows(), h.counts(), h.client.cards, h.ask.calls, self.approval_rows(h)), ([], (0, 0), [], [], []))


class AutoHoldTests(AutoBase):
    async def test_a_fixable_cliche_post_is_held_never_overridden_and_never_created(self):
        h = self.auto(slots=('06:00',))
        h.ask.plan[1] = 'cliche'
        await h.until(lambda: h.stages() == ['held'])
        self.assertEqual(h.rows()[0]['terminal_reason'], 'text_quality_held')
        for _ in range(3):
            await h.tick(300)
        self.assertEqual((h.counts(), h.pipeline.runs, h.client.cards, self.approval_rows(h)), ((0, 0), [], [], []))
        self.assertEqual(len(self.dms(h, 'text_quality_held')) + len(self.dms(h, 'chất lượng')), 1)
        h.now = slot_ts('06:00') + 60
        await h.tick(60)
        self.assertEqual(h.counts(), (0, 0))

    async def test_a_facts_post_is_held(self):
        h = self.auto(slots=('06:00',))
        h.ask.plan[1] = 'facts'
        await h.until(lambda: h.stages() == ['held'])
        for _ in range(3):
            await h.tick(300)
        self.assertEqual((h.counts(), h.pipeline.runs, self.approval_rows(h), h.posts()), ((0, 0), [], [], []))

    async def test_a_gate_that_fails_at_the_images_step_writes_no_approval(self):
        h = self.auto(slots=('06:00',))
        with patch.object(OnePost, 'verify_images', side_effect=AdminError('image_provenance_changed')):
            await h.until(lambda: h.stages() == ['held'])
        self.assertEqual((self.approval_rows(h), h.counts(), h.client.cards), ([], (0, 0), []))

    async def test_a_draft_snapshot_changed_after_the_auto_approval_never_publishes(self):
        h = self.auto(slots=('06:00',))
        original = ApprovalStore.auto_approve

        def tampering(store, kind, run_id, snapshot, *args, **kwargs):
            found = original(store, kind, run_id, snapshot, *args, **kwargs)
            if kind == 'draft':                                    # the Draft changes right after its approval row
                with store.state.connection() as db:
                    db.execute("UPDATE posts SET admin_id='tampered' WHERE admin_id IS NOT NULL")
            return found
        with patch.object(ApprovalStore, 'auto_approve', tampering):
            await h.until(lambda: h.stages() == ['held'])
        self.assertEqual(h.rows()[0]['terminal_reason'], 'draft_approval_snapshot_changed')
        h.now = slot_ts('06:00') + 60
        for _ in range(3):
            await h.tick(60)
        self.assertEqual(h.counts(), (1, 0))
        self.assertNotIn('published', [p['state'] for p in h.posts()])
        self.assertNotIn('scheduled', [p['state'] for p in h.posts()])


class AutoCommandTests(AutoBase):
    async def test_hold_before_the_slot_stops_the_post(self):
        h = self.auto(slots=('06:00',))
        await h.until_stage('scheduled')
        [post] = h.posts()
        [reply] = await h.owner(f"/nailblog hold {post['id']} chặn thử")
        self.assertIn('đã giữ', reply)
        h.now = slot_ts('06:00') + 60
        for _ in range(3):
            await h.tick(60)
        self.assertEqual(h.counts(), (1, 0))
        self.assertEqual([p['state'] for p in h.posts()], ['held'])
        self.assertEqual(h.stages(), ['held'])

    async def test_ok_and_reject_are_refused_while_self_approving(self):
        h = self.auto(slots=('06:00',))
        await h.until_stage('scheduled')
        before = self.approval_rows(h)
        for text in ('/nailblog ok 1', '/nailblog ok 2 override vì sao', '/nailblog reject 1 không thích'):
            [reply] = await h.owner(text)
            self.assertIn('tự duyệt đang bật', reply)
        self.assertEqual(self.approval_rows(h), before)


class AutoApproveStoreTests(unittest.TestCase):
    def store(self):
        from nailblog.state import State
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        return ApprovalStore(State(str(Path(temp.name) / 'a.sqlite3')))

    def rows(self, store):
        with store.state.connection() as db:
            return [dict(r) for r in db.execute('SELECT * FROM approval_requests')]

    def test_it_records_one_verified_row_and_is_idempotent(self):
        store = self.store()
        first = store.auto_approve('images', 'run-1', 'snap-a', 500.0, 100.0, quality=PASS)
        self.assertEqual(store.auto_approve('images', 'run-1', 'snap-a', 500.0, 101.0, quality=PASS), first)
        [row] = self.rows(store)
        self.assertEqual((row['status'], row['decided_by'], row['reason'], row['override'], row['requires_override'],
                          row['snapshot'], row['kind'], row['run_id']),
                         ('approved', 'auto', REASON, 0, 0, 'snap-a', 'images', 'run-1'))
        self.assertTrue(store.is_approved('run-1', 'images', 'snap-a'))
        self.assertFalse(store.is_approved('run-1', 'images', 'snap-b'))
        self.assertFalse(store.is_approved('run-1', 'draft', 'snap-a'))

    def test_it_refuses_a_request_that_requires_an_override(self):
        store = self.store()
        with self.assertRaisesRegex(ApprovalError, 'not_auto_approvable'):
            store.auto_approve('images', 'run-1', 'snap-a', 500.0, 100.0, quality=PASS, requires_override=True)
        self.assertEqual(self.rows(store), [])

    def test_it_refuses_issues_and_any_verdict_but_pass(self):
        store = self.store()
        issue = {'code': 'cliche', 'message': 'x', 'overridable': True, 'kind': 'fixable'}
        for quality, issues in ((PASS, [issue]), ({'verdict': 'fixable', 'issues': [issue]}, None),
                                ({'verdict': 'hold', 'issues': []}, None), (None, None), ({'verdict': 'pass', 'issues': [issue]}, None),
                                ({'verdict': 'pass', 'issues': []}, [{'code': 'facts', 'overridable': False}])):
            with self.assertRaisesRegex(ApprovalError, 'not_auto_approvable'):
                store.auto_approve('draft', 'run-1', 'snap-a', 500.0, 100.0, quality=quality, issues=issues)
        self.assertEqual(self.rows(store), [])

    def test_it_never_touches_an_override_request_or_a_rejected_row(self):
        store = self.store()
        issue = {'code': 'cliche', 'message': 'x', 'overridable': True, 'kind': 'fixable'}
        store.request('images', 'run-1', 'snap-a', 500.0, 100.0, requires_override=True, issues=[issue])
        with self.assertRaisesRegex(ApprovalError, 'not_auto_approvable'):
            store.auto_approve('images', 'run-1', 'snap-a', 500.0, 101.0, quality=PASS)
        rid = store.request('draft', 'run-1', 'snap-a', 500.0, 100.0)
        store.decide(rid, 'reject', 'no', 5, 5, 101.0)
        with self.assertRaisesRegex(ApprovalError, 'not_auto_approvable'):
            store.auto_approve('draft', 'run-1', 'snap-a', 500.0, 102.0, quality=PASS)
        self.assertEqual([r['status'] for r in self.rows(store)], ['pending', 'rejected'])


class ConfigTests(ConfigCase):
    def test_default_is_true_and_false_needs_staging(self):
        self.assertIs(self.load().autoflow.owner_approval, True)
        self.invalid('owner_approval = false requires contract.environment = staging', autoflow='owner_approval = false')
        self.invalid('autoflow.owner_approval must be true or false', autoflow='owner_approval = "no"')
        config = self.load(LIVE, **live_sections(
            autoflow='enabled = true\nowner_approval = false\nruns_dir = "/var/lib/agentfleet/nailblog/runs"'))
        self.assertIs(config.autoflow.owner_approval, False)


if __name__ == '__main__':
    unittest.main()
