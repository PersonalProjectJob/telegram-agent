"""AdminStore.approve honours an Owner override only on the exact, fully-checked evidence (real sqlite, no fakes)."""
import json
import tempfile
import unittest
from datetime import timedelta

from nailblog.admin_state import AdminStore
from nailblog.approvals import ApprovalStore
from nailblog.publication import AdminError
from nailblog.state import State
from tests.test_admin_phase2 import POST_ID, SLOT, config, document, payload

RUN, IMG, DRAFT = 'auto-run-1', 'snap-images', 'snap-draft'
CLICHE = {'code': 'cliche', 'kind': 'fixable', 'field': 'sections.0', 'message': 'sáo rỗng', 'overridable': True}
QUALITY_ISSUE = {k: CLICHE[k] for k in ('code', 'kind', 'field', 'message')}
NOW = (SLOT - timedelta(hours=3)).timestamp()


class OverrideApproveTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.c = config(tmp.name)
        self.s = State(self.c.db_path)
        self.store, self.approvals = AdminStore(self.s, self.c), ApprovalStore(self.s)

    def held_post(self, quality=None):
        doc = document()
        doc['quality'] = quality or {'verdict': 'fixable', 'issues': [QUALITY_ISSUE]}
        p = payload()
        self.post = self.s.add_post(SLOT.isoformat(), draft=doc)
        self.store.claim_create(self.post, p)
        self.store.record_draft(self.post, {**p, 'id': POST_ID})

    def card(self, kind, snapshot, *, issues=(CLICHE,), reason='ok vì bài vẫn dùng được', decide=True):
        rid = self.approvals.request(kind, RUN, snapshot, NOW + 10, NOW, requires_override=True, issues=list(issues))
        if decide:
            out = self.approvals.decide(rid, 'ok', reason, 7, 7, NOW, override=True)
            self.assertTrue(out['ok'], out)
        return rid

    def both(self, **kw):
        self.card('images', IMG, **kw)
        self.card('draft', DRAFT, **kw)

    def args(self, **kw):
        return {'approvals': self.approvals, 'run_id': RUN, 'images_snapshot': IMG, 'draft_snapshot': DRAFT, **kw}

    def refused(self, **kw):
        with self.assertRaisesRegex(AdminError, '^quality_pass_required$'):
            self.store.approve(self.post, NOW, override=self.args(**kw))
        self.assertEqual(self.s.get_post(self.post)['state'], 'review')

    def update_cards(self, sql, *params):
        with self.s.connection() as db:
            db.execute(sql, params)

    def test_valid_override_approves_without_faking_the_verdict_and_records_who_and_why(self):
        self.held_post()
        self.both()
        self.store.approve(self.post, NOW, override=self.args())
        row = self.s.get_post(self.post)
        self.assertEqual(row['state'], 'scheduled')
        self.assertEqual(json.loads(row['draft_json'])['quality']['verdict'], 'fixable')
        evidence = json.loads(row['details'])['override']
        self.assertEqual((evidence['by'], evidence['reason'], evidence['issues']),
                         (7, 'ok vì bài vẫn dùng được', ['cliche']))

    def test_without_override_arguments_a_held_draft_is_still_refused(self):
        self.held_post()
        self.both()
        with self.assertRaisesRegex(AdminError, '^quality_pass_required$'):
            self.store.approve(self.post, NOW)

    def test_an_approved_row_without_override_flag_is_refused(self):
        self.held_post()
        self.card('images', IMG, decide=False)
        self.card('draft', DRAFT, decide=False)
        self.update_cards("UPDATE approval_requests SET status='approved', reason='x', decided_by=7, override=0")
        self.refused()

    def test_empty_reason_is_refused(self):
        self.held_post()
        self.both()
        self.update_cards("UPDATE approval_requests SET reason=''")
        self.refused()

    def test_only_one_of_the_two_cards_overridden_is_refused(self):
        self.held_post()
        self.card('images', IMG)
        self.card('draft', DRAFT, decide=False)
        self.refused()

    def test_wrong_snapshot_or_run_is_refused(self):
        self.held_post()
        self.both()
        self.refused(draft_snapshot='snap-other')
        self.refused(images_snapshot='snap-other')
        self.refused(run_id='another-run')

    def test_a_rejected_card_is_refused(self):
        self.held_post()
        self.card('images', IMG)
        rid = self.card('draft', DRAFT, decide=False)
        self.approvals.decide(rid, 'reject', 'không', 7, 7, NOW)
        self.refused()

    def test_a_new_issue_after_the_override_is_refused(self):
        self.held_post(quality={'verdict': 'fixable', 'issues': [
            QUALITY_ISSUE, {'code': 'para_len', 'kind': 'fixable', 'field': 'b', 'message': ''}]})
        self.both()
        self.refused()

    def test_facts_han_and_medical_issues_are_refused_even_with_a_forged_override_row(self):
        for code in ('facts', 'unexpected_han', 'medical_claim'):
            with self.subTest(code=code):
                self.update_cards('DELETE FROM approval_requests')
                self.update_cards('DELETE FROM admin_operations')
                self.update_cards('DELETE FROM posts')
                self.held_post(quality={'verdict': 'fixable', 'issues': [{**QUALITY_ISSUE, 'code': code}]})
                self.both()   # request()/decide() would never store these issues: forge them
                self.update_cards('UPDATE approval_requests SET issues=?', json.dumps([{**CLICHE, 'code': code}]))
                self.refused()

    def test_a_hold_verdict_is_refused(self):
        self.held_post(quality={'verdict': 'hold', 'issues': [{**QUALITY_ISSUE, 'code': 'h1_in_content', 'kind': 'hold'}]})
        self.both()
        self.refused()

    def test_stored_issues_not_marked_overridable_are_refused(self):
        self.held_post()
        self.both()
        self.update_cards('UPDATE approval_requests SET issues=?', json.dumps([{**CLICHE, 'overridable': False}]))
        self.refused()


if __name__ == '__main__':
    unittest.main()
