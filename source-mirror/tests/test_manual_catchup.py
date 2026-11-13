"""Manual-catchup guard tests: synthetic fixtures, no network or publication."""
import json,sys,tempfile,unittest
from contextlib import ExitStack
from datetime import datetime,timedelta
from pathlib import Path
from unittest.mock import Mock,patch
from zoneinfo import ZoneInfo

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from source_mirror.official_deadline import manual_seconds_available,start_manual_window,manual_window_seconds
from source_mirror.publisher import original_attempt_seconds
from source_mirror.scheduled_assignments import require_manual_unpinned,ScheduleInvalid
from source_mirror.store import Store,local_day,ZONE
from source_mirror.localization import QualityHold
from source_mirror import __main__ as runner,topics

class WindowTests(unittest.TestCase):
 def test_window_cannot_roll_forward_after_slot_or_clock_jump(self):
  start=datetime(2026,11,5,11,40,tzinfo=ZONE);window=start_manual_window('2026-11-05',start,1000)
  self.assertEqual(manual_window_seconds('2026-11-05',window,start,1000),900)
  for at,mono in [(start+timedelta(minutes=20),2200),(start+timedelta(minutes=21),2260),(start-timedelta(hours=1),2000)]:
   self.assertLessEqual(manual_window_seconds('2026-11-05',window,at,mono),0)
  self.assertLess(manual_window_seconds('2026-11-05',window,start+timedelta(minutes=40),1001),0)
 def test_midnight_and_near_next_slot_fail_closed(self):
  late=datetime(2026,11,5,23,50,tzinfo=ZONE);w=start_manual_window('2026-11-05',late,10)
  self.assertEqual(manual_window_seconds('2026-11-05',w,late,10),300)
  with self.assertRaises(ValueError):manual_window_seconds('2026-11-05',w,late+timedelta(minutes=11),671)
  self.assertLess(manual_seconds_available('2026-11-05',datetime(2026,11,5,11,56,tzinfo=ZONE)),0)
  with self.assertRaises(ValueError):manual_seconds_available('2026-11-05',datetime(2026,11,5,12,0,tzinfo=ZONE))
  with self.assertRaises(ValueError):manual_seconds_available('2026-11-05',datetime(2026,11,5,1,0))
 def test_null_slot_requires_explicit_window_and_scheduled_cannot_use_it(self):
  now=datetime(2026,11,5,1,0,tzinfo=ZONE);w=start_manual_window('2026-11-05',now,100)
  with self.assertRaises(ValueError):original_attempt_seconds('2026-11-05',None,None,now,100)
  with self.assertRaises(ValueError):original_attempt_seconds('2026-11-05','08:00',w,now,100)
  self.assertGreater(original_attempt_seconds('2026-11-05',None,w,now,100),0)
 def test_current_and_future_pins_are_protected(self):
  aid='a'*64
  for mapping in [{('2026-11-05','12:00'):aid},{('2026-11-06','12:00'):aid}]:
   with self.assertRaises(ScheduleInvalid):require_manual_unpinned(aid,'2026-11-05',loader=lambda:mapping)
  require_manual_unpinned(aid,'2026-11-05',loader=lambda:{('2026-11-04','12:00'):aid})

class OnceTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.store=Store(self.root);self.store.set_paused(False);self.aid='a'*64
  self.folder=self.root/'fixture';self.folder.mkdir();(self.folder/'metadata.json').write_text(json.dumps({'content_origin':'ai_original'}));(self.folder/'content.json').write_text('[]')
  self.store.db.execute("insert into articles values (?,?,?,null,'ready',null,?,?)",(self.aid,'urn:fixture','Business title','2026-11-05','manifest'))
  self.candidate=dict(self.store.db.execute('select * from articles where id=?',(self.aid,)).fetchone())
  self.stack=ExitStack();self.addCleanup(self.temp.cleanup);self.addCleanup(self.store.close);self.addCleanup(self.stack.close)
  def p(name,**kwargs):return self.stack.enter_context(patch(name,**kwargs))
  p('source_mirror.publisher.preflight');self.publish=p('source_mirror.publisher.publish',return_value={'url':'https://nailhub.ai/blog/fixture','published_day':local_day()})
  p('source_mirror.topics.candidates',return_value=[self.candidate]);p('source_mirror.store.verify_snapshot',return_value=self.folder);p('source_mirror.store.sha',return_value='manifest')
  self.source=p('source_mirror.original_business.verify_original');p('source_mirror.localization.generate_revision',return_value='revision');p('source_mirror.localization.load_approved',return_value={'article_id':self.aid})
  self.duplicate=p('source_mirror.original_duplicate_scan.precheck');p('source_mirror.scratch_cover.prepare_cover',return_value={'kind':'source'});p('source_mirror.scratch_cover.validate_cover')
  p('source_mirror.scheduled_assignments.require_manual_unpinned');p('source_mirror.official_deadline.manual_window_seconds',return_value=500)
  self.stack.enter_context(patch.object(self.store,'safe_export_reports'))
 def run_once(self):return runner.once({},self.store,requested_article_id=self.aid,manual_catchup=True)
 def test_explicit_article_uses_real_day_no_slot_and_no_unrelated_selection(self):
  self.assertEqual(self.run_once(),'https://nailhub.ai/blog/fixture')
  row=self.store.db.execute('select reserved_day,state from attempts').fetchone();phase=self.store.db.execute('select slot from attempt_phases').fetchone()
  self.assertEqual(tuple(row),(local_day(),'published'));self.assertIsNone(phase['slot']);self.assertEqual(self.store.db.execute('select count(*) from slots').fetchone()[0],0)
  article=self.publish.call_args.args[2];self.assertIsInstance(article['_manual_catchup_window'],tuple);self.source.assert_called();self.duplicate.assert_called_once()
 def test_reject_source_or_duplicate_without_attempt(self):
  for which in ('source','duplicate'):
   with self.subTest(which=which):
    (self.source if which=='source' else self.duplicate).side_effect=QualityHold('held')
    with self.assertRaises(QualityHold):self.run_once()
    self.assertEqual(self.store.db.execute('select count(*) from attempts').fetchone()[0],0);self.publish.assert_not_called()
    self.source.side_effect=None;self.duplicate.side_effect=None
 def test_uncertain_publish_is_not_retried(self):
  self.publish.side_effect=TimeoutError('unknown outcome')
  with self.assertRaises(TimeoutError):self.run_once()
  self.assertEqual(self.store.db.execute('select state from attempts').fetchone()[0],'unknown');self.publish.assert_called_once()
 def test_non_original_and_fake_day_rejected(self):
  (self.folder/'metadata.json').write_text(json.dumps({'content_origin':'scratch'}))
  with self.assertRaises(QualityHold):self.run_once()
  self.assertEqual(self.store.db.execute('select count(*) from attempts').fetchone()[0],0)
  with self.assertRaises(ValueError):runner.once({},self.store,day='2026-11-04',requested_article_id=self.aid,manual_catchup=True)
 def test_pending_attempt_blocks_without_publish(self):
  self.store.db.execute("insert into attempts(article_id,reserved_day,state,created_at) values (?,?,'unknown','now')",(self.aid,local_day()))
  with self.assertRaisesRegex(ValueError,'unresolved'):self.run_once()
  self.publish.assert_not_called()
 def test_current_day_quota_blocks_without_publish(self):
  for i in range(4):
   aid=str(i)*64;self.store.db.execute("insert into articles values (?,?,?,null,'published',null,?,?)",(aid,'urn:'+aid,'x','now','x'))
   self.store.db.execute("insert into attempts(article_id,reserved_day,state,published_day,created_at) values (?,?,'published',?,'now')",(aid,local_day(),local_day()))
  with self.assertRaisesRegex(ValueError,'quota'):self.run_once()
  self.publish.assert_not_called()

class SelectionTests(unittest.TestCase):
 def test_filter_before_classification_avoids_touching_other_ready_records(self):
  store=Mock();store.db.execute.return_value.fetchall.return_value=[];store.ready_candidates.return_value=[{'id':'a'*64},{'id':'b'*64}]
  with tempfile.TemporaryDirectory() as folder:
   base=Path(folder);(base/'metadata.json').write_text(json.dumps({'title':'Salon business'}),encoding='utf8');(base/'content.json').write_text(json.dumps([{'type':'html','html':'<p>nail salon business</p>'}]),encoding='utf8')
   with patch('source_mirror.topics.classify_article',return_value='business') as classify, patch('source_mirror.topics.verify_snapshot',return_value=base), patch('source_mirror.topics.official_business_source',return_value=True):
    found=topics.candidates(store,'2026-11-05',None,limit=None,only_article_id='a'*64)
  self.assertEqual([x['id'] for x in found],['a'*64]);self.assertEqual(classify.call_count,1);store.quality_hold.assert_not_called()

if __name__=='__main__':unittest.main()
