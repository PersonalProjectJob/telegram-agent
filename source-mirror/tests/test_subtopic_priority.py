"""Tests for editorial preference; no production publication is performed."""
import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from source_mirror.slot_policy import allows, subtopic_rank
from source_mirror.__main__ import once
from source_mirror.store import Store
from source_mirror.snapshot import save
from source_mirror.topics import candidates


class SubtopicPriorityTests(unittest.TestCase):
    def test_preference_and_fallback_across_business_slots(self):
        titles={'08:00':'Nail salon appointment scheduling and workflow',
                '16:00':'Marketing and social media for nail salon owners',
                '20:00':'Planning salon expansion and capital for nail salon owners'}
        for slot,title in titles.items():
            with self.subTest(slot=slot):
                self.assertTrue(allows(slot,title))
                self.assertEqual(subtopic_rank(title,'',slot),0)
                for other in set(titles)-{slot}:
                    self.assertEqual(subtopic_rank(title,'',other),1)
        self.assertTrue(allows('12:00','Nail art tutorial'))
        self.assertFalse(allows('12:00',titles['08:00']))

    def test_title_wins_over_unrelated_body_mentions(self):
        title='Marketing for nail salon customers'
        self.assertEqual(subtopic_rank(title,'Schedule appointments and manage inventory.','16:00'),0)
        self.assertEqual(subtopic_rank(title,'Schedule appointments and manage inventory.','08:00'),1)
        self.assertEqual(subtopic_rank('A guide for nail salon owners','Review appointments and staffing.','08:00'),0)
        self.assertEqual(subtopic_rank('A guide for nail salon owners','General ideas','08:00'),1)

    def test_business_order_tries_preferred_original_before_other_subtopics(self):
        db=Mock()
        def execute(sql,*args):
            result=Mock()
            result.fetchone.return_value=(0,) if 'count(*)' in sql else None
            return result
        db.execute.side_effect=execute
        store=SimpleNamespace(db=db,root='root',paused=lambda:False,recover_closed_uploads=Mock())
        events=[]
        def official(store,day,slot,priority=None):
            events.append(('official',priority));return 'fallback-url' if priority==1 else False
        def rank(store,article,slot):return article['rank']
        def allowed(store,article,slot):events.append(('article',article['id']));return False
        def original(*args):events.append(('generate',None));return None
        articles=[{'id':'fallback','rank':1},{'id':'preferred','rank':0}]
        with patch('source_mirror.publisher.preflight'),patch('source_mirror.official_scheduler.try_once',side_effect=official), \
             patch('source_mirror.slot_policy.priority_for_article',side_effect=rank), \
             patch('source_mirror.topics.candidates',return_value=articles), \
             patch('source_mirror.topics.matches_slot',side_effect=allowed), \
             patch('source_mirror.official_deadline.seconds_available',return_value=3000), \
             patch('source_mirror.original_business.prepare',side_effect=original):
            self.assertEqual(once({},store,'2026-11-05','16:00'),'fallback-url')
        self.assertEqual(events,[('official',0),('article','preferred'),('generate',None),('official',1)])

    def test_real_snapshot_queue_prioritizes_and_falls_back(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        store=Store(Path(tmp.name));self.addCleanup(store.close)
        articles=[('growth','Growth planning for nail salon owners'),
                  ('marketing','Marketing and social media for nail salon owners'),
                  ('operations','Nail salon appointment scheduling and workflow')]
        ids={}
        for slug,title in articles:
            meta={'source_url':'https://example.com/'+slug,'title':title}
            ids[slug]=save(store.root,meta,[{'type':'html','html':'<p>Salon business ideas.</p>'}],'<p>Salon business ideas.</p>',[])
            store.add_ready(meta['source_url'],title)
        with patch('source_mirror.topics.official_business_source',return_value=True):
            for slot,slug in (('08:00','operations'),('16:00','marketing'),('20:00','growth')):
                with self.subTest(slot=slot):
                    queue=candidates(store,'2026-11-05',slot,limit=None)
                    self.assertEqual(queue[0]['id'],ids[slug])
                    self.assertEqual({x['id'] for x in queue},set(ids.values()))
                    self.assertEqual(candidates(store,'2026-11-05',slot,limit=1)[0]['id'],ids[slug])
            self.assertEqual(candidates(store,'2026-11-05','12:00',limit=None),[])

    def test_failed_focused_generation_reaches_business_fallback(self):
        from source_mirror.localization import QualityHold
        db=Mock()
        def execute(sql,*args):
            result=Mock();result.fetchone.return_value=(0,) if 'count(*)' in sql else None;return result
        db.execute.side_effect=execute
        store=SimpleNamespace(db=db,root='root',paused=lambda:False,recover_closed_uploads=Mock())
        events=[]
        def official(store,day,slot,priority=None):
            events.append(priority);return 'fallback-url' if priority==1 else False
        with patch('source_mirror.publisher.preflight'),patch('source_mirror.official_scheduler.try_once',side_effect=official), \
             patch('source_mirror.topics.candidates',return_value=[]), \
             patch('source_mirror.official_deadline.seconds_available',return_value=3000), \
             patch('source_mirror.original_business.prepare',side_effect=QualityHold('no original')):
            self.assertEqual(once({},store,'2026-11-05','16:00'),'fallback-url')
        self.assertEqual(events,[0,1])


if __name__=='__main__':unittest.main()
