"""Old content requires this group's real Telegram reply, including recovery."""
from dataclasses import replace
from unittest import TestCase

from tg_triage.autonomous import resume_case
from tg_triage.classifier import Classifier
from tg_triage.followups import sweep
from tg_triage.pipeline import Pipeline
from tg_triage.store import Store
from tests.helpers import config, message, FakeHTTP, FakeGitHub
from tests.test_autonomous import Router, route, draft
from tests.test_incident_context import response, wire
from tests import test_followups as followup_fixtures
from tests.test_followups import answer


class ReplyAuthorityTests(TestCase):
    def build(self,*answers):
        store=Store(':memory:');self.addCleanup(store.close)
        p=Pipeline(replace(config('live'),autonomous_enabled=True),store,Router(*answers),FakeGitHub(),
                   notice_sender=lambda c,m,t:900+m)
        return p

    def test_no_reply_cannot_promote_matching_pending_root(self):
        for action in ('CONTINUE','CLARIFY'):
            with self.subTest(action=action):
                p=self.build([route([1],'CLARIFY')],[route([2],action,'c1',draft([1,2]))])
                p.process(message(1,text='Login đúng pass mà không login được'))
                before=dict(p.store.get(message().chat_id,1))
                result=p.process(message(2,text='Login đúng pass mà không login được',mentions_listener=False))
                self.assertEqual('pending_review',result.decision)
                self.assertFalse(p.github.calls);self.assertEqual([],p.classifier.calls[1][1])
                self.assertEqual(before,dict(p.store.get(message().chat_id,1)))

    def test_no_reply_matching_closed_ticket_is_new_without_related_link(self):
        p=self.build([route([1])],[route([2])])
        p.process(message(1));before=dict(p.store.get(message().chat_id,1))
        p.github.lifecycle_snapshot=lambda *a: self.fail('No Reply must not inspect/update old ticket')
        p.process(message(2,mentions_listener=False))
        self.assertEqual(2,len(p.github.calls));self.assertEqual([],p.classifier.calls[1][1])
        self.assertNotIn('Ticket liên quan:',p.github.calls[1].body)
        self.assertEqual(before,dict(p.store.get(message().chat_id,1)))

    def test_group_cannot_borrow_sibling_reply_authority(self):
        p=self.build([route([1])],[route([2],'CONTINUE','c1'),route([3],'CLARIFY','c1')])
        p.process(message(1));before=dict(p.store.get(message().chat_id,1))
        p.store.enqueue(message(3,text='Cùng lỗi nhưng không reply',mentions_listener=False))
        p.process(message(2,reply_to_id=1,mentions_listener=False))
        self.assertEqual([2],p.classifier.calls[1][1][0]['reply_source_ids'])
        self.assertEqual(1,len(p.github.calls));self.assertEqual(before,dict(p.store.get(message().chat_id,1)))
        self.assertEqual(0,p.store.connection.execute('SELECT COUNT(*) FROM followup_jobs').fetchone()[0])

    def test_direct_classifier_rejects_optional_candidate_continuation_without_reply(self):
        for action in ('CONTINUE','CLARIFY'):
            with self.subTest(action=action):
                choice=dict(candidate_id='c1',pending=True,exact_reply=False,reply_source_ids=[],messages=[message(1)])
                http=FakeHTTP(response(wire([2],action=action,candidate='c1')))
                classifier=Classifier(config().ollama,opener=http)
                self.assertIsNone(classifier.analyze_ingress([message(2)],[choice]))
                self.assertEqual('invalid_ingress_output',classifier.last_ingress_outcome)

    def legacy_plan(self,p,mid=2):
        p.process(message(1))
        incoming=message(mid,mentions_listener=False);p.store.enqueue(incoming)
        candidate=p.store.autonomy_candidates(incoming,p.config.gh_repo)[0]
        candidate.update(candidate_id='c1',reply_source_ids=[],exact_reply=False)
        group=route([mid],'CONTINUE','c1',draft([1,mid]));group['candidate']=candidate
        p.store.save_autonomous_plans([incoming],[group],p.config.gh_repo)
        return incoming,candidate

    def test_cached_implicit_pending_plan_cannot_execute_after_upgrade(self):
        p=self.build([route([1],'CLARIFY')]);incoming,_=self.legacy_plan(p)
        before=dict(p.store.get(incoming.chat_id,1))
        result=p.process(incoming)
        self.assertEqual('cached_reply_authority_revoked',result.reason)
        self.assertFalse(p.github.calls);self.assertEqual(before,dict(p.store.get(incoming.chat_id,1)))

    def test_periodic_resume_implicit_pending_plan_cannot_execute_after_upgrade(self):
        p=self.build([route([1],'CLARIFY')]);incoming,candidate=self.legacy_plan(p)
        p.store.start_classifying(incoming)
        key,_,_=p.store.autonomy_case([incoming],candidate)
        with p.store.connection:p.store.connection.execute("UPDATE cases SET status='open' WHERE case_key=?",(key,))
        result=resume_case(p,key)
        self.assertEqual('cached_reply_authority_revoked',result.reason);self.assertFalse(p.github.calls)

    def test_queued_planned_ambiguous_followup_recovery_requires_original_reply(self):
        for state in ('queued','planned','ambiguous'):
            with self.subTest(state=state):
                helper=followup_fixtures.FollowupTests('test_split_two_total_and_reply_new_message_preserves_original_fields')
                store,model,gh,sent,p=helper.setup_case(count=1);self.addCleanup(store.close)
                actual=message(10,text='Bổ sung cùng lỗi nhưng không reply',mentions_listener=False)
                store.start_classifying(actual)
                source=store.followup_source(message(10,reply_to_id=901))
                store.enqueue_followup(actual,source)
                job=store.pending_followups()[0]
                if state!='queued':
                    store.save_followup_plan(job,answer('update'),[('comment',dict(number=101,body='Old planned write'))])
                if state=='ambiguous':
                    with store.connection:
                        store.connection.execute("UPDATE followup_jobs SET status='ambiguous' WHERE id=?",(job['id'],))
                        store.connection.execute("UPDATE followup_operations SET status='ambiguous',reserved_at='unknown' WHERE job_id=?",(job['id'],))
                operations_before=store.followup_operations(job)
                sweep(p)
                self.assertFalse(model.followups);self.assertFalse(gh.comments);self.assertEqual(1,len(gh.calls))
                self.assertEqual('blocked',store.connection.execute('SELECT status FROM followup_jobs').fetchone()[0])
                self.assertEqual(operations_before,store.followup_operations(job))
                self.assertIsNone(store.get(actual.chat_id,10)['reply_to_id'])

    def test_reply_within_unlinked_job_is_not_old_root_authority(self):
        helper=followup_fixtures.FollowupTests('test_split_two_total_and_reply_new_message_preserves_original_fields')
        store,model,gh,sent,p=helper.setup_case(count=1);self.addCleanup(store.close)
        sources=[message(10,text='Unlinked new request'),message(11,text='Details',reply_to_id=10)]
        for m in sources:store.start_classifying(m)
        store.enqueue_followup_batch(sources,store.followup_source(message(10,reply_to_id=901)))
        sweep(p)
        self.assertFalse(model.followups);self.assertFalse(gh.comments)
        self.assertEqual('blocked',store.connection.execute('SELECT status FROM followup_jobs').fetchone()[0])
