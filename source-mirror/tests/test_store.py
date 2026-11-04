import tempfile, unittest, json
from unittest.mock import patch
from datetime import datetime, timezone
from pathlib import Path
from source_mirror.store import Store, identity, local_day
from source_mirror.snapshot import save
from source_mirror.crawler import validate_eligibility, validate_cursor, MAX_LISTING_PAGES, listing_page_plan, FIRST_LISTING, choose_next

class DurableStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name); self.s=Store(self.root)
    def tearDown(self): self.s.close(); self.tmp.cleanup()
    def article(self,n):
        url=f'https://example.com/post/{n}'; meta={'source_url':url,'title':f'Title {n}'}
        aid=save(self.root,meta,[{'type':'paragraph','text':'Hello'}],'<p>Hello</p>',[])
        self.s.add_ready(url,meta['title'],f'2026-10-{n:02d}'); return aid
    def test_canonical_and_duplicates(self):
        self.assertEqual(identity('https://EXAMPLE.com/a?utm_source=x#x'), identity('https://example.com/a'))
        self.article(1); self.article(1)
        self.assertEqual(len(self.s.status()['articles']),1)
    def test_unknown_blocks_across_day_and_restart(self):
        self.article(1); self.article(2); self.s.set_paused(False)
        attempt,_=self.s.reserve('2026-10-28'); self.s.unknown(attempt)
        self.s.close(); self.s=Store(self.root)
        with self.assertRaisesRegex(ValueError,'unresolved'): self.s.reserve('2026-10-29')
        self.s.release(attempt,'verified no destination article')
        self.assertTrue(self.s.reserve('2026-10-29'))
    def test_quota_and_day(self):
        for i in range(1,6): self.article(i)
        self.s.set_paused(False)
        for i in range(4):
            attempt,_=self.s.reserve('2026-10-28'); self.s.published(attempt,f'https://nailhub.ai/blog/{i}','2026-10-28')
        with self.assertRaisesRegex(ValueError,'quota'): self.s.reserve('2026-10-28')
        self.assertTrue(self.s.reserve('2026-10-29'))
    def test_snapshot_corruption_prevents_publish(self):
        aid=self.article(1); self.s.set_paused(False)
        (self.root/'articles'/aid/'content.json').write_text('tampered')
        with self.assertRaisesRegex(ValueError,'corrupt'): self.s.reserve()
        self.assertEqual(self.s.status()['attempts'],[])
    def test_manifest_rewritten_is_detected(self):
        import hashlib, json
        aid=self.article(1); self.s.set_paused(False)
        folder=self.root/'articles'/aid
        (folder/'content.json').write_text('[]')
        manifest=json.loads((folder/'snapshot-manifest.json').read_text())
        manifest['files']['content.json']=hashlib.sha256(b'[]').hexdigest()
        (folder/'snapshot-manifest.json').write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError,'manifest changed'): self.s.reserve()
    def test_slot_reserved_atomically(self):
        self.article(1); self.article(2); self.s.set_paused(False)
        attempt,_=self.s.reserve('2026-10-28','08:00')
        self.s.release(attempt,'verified no destination article')
        with self.assertRaises(Exception): self.s.reserve('2026-10-28','08:00')
        self.assertEqual(len(self.s.status()['attempts']),1)
    def test_exhausted_morning_tries_leave_midday_slot_and_quota_available(self):
        aid=self.article(1); self.s.set_paused(False); day='2026-10-30'
        for _ in range(3): self.assertTrue(self.s.claim_slot_try(day,'08:00'))
        self.assertFalse(self.s.claim_slot_try(day,'08:00'))
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM attempts').fetchone()[0],0)
        self.assertTrue(self.s.claim_slot_try(day,'12:00'))
        attempt,_=self.s.reserve(day,'12:00',aid)
        self.assertIsInstance(attempt,int)
    def test_held_candidate_can_be_recaptured(self):
        url='https://example.com/post/1'
        self.s.hold(url,'ValueError')
        self.assertEqual(self.s.status()['articles'][0]['state'],'held')
        self.article(1)
        self.assertEqual(self.s.status()['articles'][0]['state'],'ready')
    def test_pre_ready_destination_eligibility(self):
        with self.assertRaises(ValueError): validate_eligibility('valid title',[{}]*11)
        with self.assertRaises(ValueError): validate_eligibility('tiny',[{}])
        with self.assertRaises(ValueError): validate_eligibility('valid title',[{'bytes':b'GIF89aabc'}],downloaded=True)
        validate_eligibility('valid title',[{'bytes':b'\xff\xd8\xffrest'}],downloaded=True)
    def test_cursor_validation_and_page_budget(self):
        self.assertEqual(MAX_LISTING_PAGES,5)
        self.assertTrue(validate_cursor('https://www.scratchmagazine.co.uk/step-by-step/page/10/'))
        self.assertEqual(validate_cursor('https://www.scratchmagazine.co.uk/step-by-step/page/2/'),'https://www.scratchmagazine.co.uk/step-by-step/page/2/')
        for url in ('https://evil.example/step-by-step/page/2/','https://www.scratchmagazine.co.uk/step-by-step/page/1/','https://www.scratchmagazine.co.uk/step-by-step/page/2/?x=1','http://www.scratchmagazine.co.uk/step-by-step/page/2/'):
            with self.assertRaises(ValueError): validate_cursor(url)
    def test_listing_plan_restart_and_budget(self):
        def url(n): return f'https://www.scratchmagazine.co.uk/step-by-step/page/{n}/'
        seen=[]
        def load(current):
            seen.append(current)
            n=1 if current==FIRST_LISTING else int(current.rstrip('/').split('/')[-1])
            return [f'article-{n}'],url(n+1)
        first=list(listing_page_plan(load))
        self.assertEqual([p[0] for p in first],[FIRST_LISTING,url(2),url(3),url(4),url(5)])
        cursor=first[-1][2]  # persisted observed next link after completed page
        resumed=list(listing_page_plan(load,cursor))
        self.assertEqual([p[0] for p in resumed],[FIRST_LISTING,url(6),url(7),url(8),url(9)])
        with self.assertRaises(ValueError): list(listing_page_plan(lambda _: ([str(i) for i in range(21)],url(2))))
        with self.assertRaises(ValueError): list(listing_page_plan(lambda _: ([],url(2)),url(2)))
    def test_reports_rebuild_idempotently_after_publication(self):
        aid=self.article(1); self.s.set_paused(False)
        attempt,_=self.s.reserve('2026-10-28'); self.s.published(attempt,'https://nailhub.ai/blog/example','2026-10-28')
        manifest=(self.root/'articles'/aid/'snapshot-manifest.json').read_bytes()
        self.s.export_reports('2026-10-28')
        result=self.root/'articles'/aid/'publication'/'result.json'
        original=result.read_bytes(); self.s.export_reports('2026-10-28')
        self.assertEqual(original,result.read_bytes())
        self.assertEqual(json.loads((self.root/'reports'/'2026-10-28.json').read_text())['published_count'],1)
        result.unlink(); self.s.export_reports('2026-10-28')
        self.assertEqual(original,result.read_bytes())
        self.assertEqual(manifest,(self.root/'articles'/aid/'snapshot-manifest.json').read_bytes())
    def test_export_failure_does_not_undo_committed_publication(self):
        self.article(1); self.s.set_paused(False)
        attempt,_=self.s.reserve('2026-10-28'); self.s.published(attempt,'https://nailhub.ai/blog/example','2026-10-28')
        with patch.object(self.s,'export_reports',side_effect=OSError('disk unavailable')):
            self.s.safe_export_reports('2026-10-28')
        self.assertEqual(self.s.status()['attempts'][0]['state'],'published')
    def test_independent_pagers_and_duplicate_next_links(self):
        next_article='https://www.scratchmagazine.co.uk/step-by-step/page/2/'
        other='https://www.scratchmagazine.co.uk/step-by-step/paged-3/2/'
        self.assertEqual(choose_next([other,next_article,next_article]),next_article)
        self.assertIsNone(choose_next([other]))
        with self.assertRaises(ValueError): choose_next([other+'?changed=1'])
        with self.assertRaises(ValueError): choose_next([next_article+'?changed=1'])
        with self.assertRaises(ValueError): choose_next(['https://evil.example/step-by-step/page/2/'])
        with self.assertRaises(ValueError): choose_next([next_article,'https://www.scratchmagazine.co.uk/step-by-step/page/3/'])
    def test_paused_initially(self):
        self.article(1)
        with self.assertRaisesRegex(ValueError,'paused'): self.s.reserve()
    def test_timezone_boundary(self):
        self.assertEqual(local_day(datetime(2026,10,28,17,tzinfo=timezone.utc)),'2026-10-29')
    def test_bad_media_never_ready(self):
        with self.assertRaises(ValueError): save(self.root,{'source_url':'https://example.com/a','title':'x'},[{'text':'x'}],'<p>x</p>',[{'bytes':b'<html>error</html>'}])
        self.assertEqual(list((self.root/'articles').iterdir()),[])

if __name__=='__main__': unittest.main()
