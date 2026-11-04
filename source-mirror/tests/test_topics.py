import tempfile
import unittest
import io
from pathlib import Path
from PIL import Image
from source_mirror.snapshot import save
from source_mirror.store import Store
from source_mirror.topics import classify, candidates, payload_contains_tax


class TopicSelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.store=Store(self.root)
    def tearDown(self):
        self.store.close();self.tmp.cleanup()
    def add(self, slug, title, body='A nail tutorial.', alt=None):
        meta={'source_url':'https://example.com/'+slug,'title':title}
        media=[]
        if alt is not None:
            img=io.BytesIO();Image.new('RGB',(2,2)).save(img,format='PNG')
            media=[{'bytes':img.getvalue(),'alt':alt}]
        aid=save(self.root,meta,[{'type':'html','html':'<p>'+body+'</p>'}],'<p>source</p>',media)
        self.store.add_ready(meta['source_url'],title)
        return aid
    def published(self, aid, day):
        self.store.db.execute("INSERT INTO attempts(article_id,reserved_day,state,published_day,created_at) VALUES (?,?,'published',?,'2026-10-29T00:00:00Z')",(aid,day,day))
        self.store.db.execute("UPDATE articles SET state='published' WHERE id=?",(aid,))
    def test_slot_preferences_and_no_halloween_when_regular_available(self):
        a=self.add('ghost','Halloween ghost art'); b=self.add('shop','How to build a nail salon'); c=self.add('marble','Luxury marble nails'); d=self.add('other','A short nail tutorial')
        self.assertEqual([r['id'] for r in candidates(self.store,'2026-10-29','12:00')],[c,b,d])
        self.assertEqual([r['id'] for r in candidates(self.store,'2026-10-29','16:00')],[b,c,d])
        self.assertNotIn(a,[r['id'] for r in candidates(self.store,'2026-10-29','20:00')])
    def test_seasonal_detected_in_body_and_seven_day_cap(self):
        a=self.add('look','An elegant nail look','A ghost for Halloween.')
        prior=self.add('pumpkin','Pumpkin style');self.published(prior,'2026-10-24')
        self.assertEqual(candidates(self.store,'2026-10-29','12:00'),[])
        self.assertEqual([r['id'] for r in candidates(self.store,'2026-10-31','12:00')],[a])
    def test_tax_in_body_held_durably_without_attempt_or_slot(self):
        aid=self.add('salon','Salon owner guide','Review 1099 payroll and IRS tax obligations.')
        self.assertEqual(candidates(self.store,'2026-10-29','12:00'),[])
        self.store.hold('https://example.com/salon','recrawl failure')
        self.store.add_ready('https://example.com/salon','Salon owner guide')
        row=self.store.db.execute('SELECT state,reason FROM articles WHERE id=?',(aid,)).fetchone()
        self.assertEqual(row['state'],'held');self.assertTrue(row['reason'].startswith('topic:'))
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM attempts').fetchone()[0],0)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM slots').fetchone()[0],0)
    def test_scan_beyond_eight_seasonal_candidates(self):
        good=self.add('classy','Luxury nail look')
        for n in range(12): self.add('ghost'+str(n),'Halloween ghost '+str(n))
        self.assertEqual([r['id'] for r in candidates(self.store,'2026-10-29','12:00')],[good])
    def test_terms_are_whole_words_and_tax_precedence(self):
        self.assertEqual(classify('Nail tip texture inspiration'),'other')
        self.assertEqual(classify('Halloween nail salon tax guide'),'tax_review')
        for text in ('form W-4', 'Form 941', 'tip reporting', 'qualified tips deduction', 'employee vs independent contractor'):
            with self.subTest(text=text):self.assertEqual(classify(text),'tax_review')
        for text in ('hướng dẫn thuế cho chủ tiệm', 'quy định khai thue', 'IRS.gov', 'khấu trừ thu nhập', 'Form 1040', '#ThuếNail', 'thuê\u0301'):
            with self.subTest(text=text):self.assertEqual(classify(text),'tax_review')
    def test_generated_tax_topic_is_held_before_reservation(self):
        fields={'title':'Mẫu móng đẹp','summary':'Gợi ý phong cách','meta_title':'Móng đẹp','meta_description':'Mẫu nail mới', 'blocks':[{'type':'html','html':'<p>Mẫu nail cho tiệm.</p>'}]}
        payload={'locales':{'Vietnamese':dict(fields),'English':dict(fields)},'hashtags':['nailart']}
        self.assertFalse(payload_contains_tax(payload))
        payload['locales']['Vietnamese']['meta_description']='Hướng dẫn khai thuế cho chủ tiệm'
        self.assertTrue(payload_contains_tax(payload))
        payload['locales']['Vietnamese']['meta_description']='Mẫu nail mới'
        for tag in ('TaxTips', 'khai_thue'):
            payload['hashtags']=[tag]
            self.assertTrue(payload_contains_tax(payload))
    def test_seasonality_in_media_alt_is_not_selected_over_regular_article(self):
        self.add('elegant','Elegant manicure',alt='Halloween ghost nail')
        clean=self.add('classic','Classic nail design')
        self.assertEqual([r['id'] for r in candidates(self.store,'2026-10-29','12:00')],[clean])
    def test_policy_can_disable_halloween_and_cannot_disable_tax_review(self):
        self.add('ghost','Halloween ghost')
        self.assertEqual(candidates(self.store,'2026-10-29','12:00',config={'topics':{'halloween_max_per_7_days':0}}),[])
        for bad in ({'us_tax_requires_human_review':False},{'halloween_max_per_7_days':2},{'halloween_max_per_7_days':True},{'unknown':1}):
            with self.subTest(bad=bad),self.assertRaises(ValueError):
                candidates(self.store,'2026-10-29','12:00',config={'topics':bad})


if __name__=='__main__':unittest.main()
