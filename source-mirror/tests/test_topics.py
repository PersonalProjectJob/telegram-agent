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
        url=slug if slug.startswith('https://') else 'https://example.com/'+slug
        meta={'source_url':url,'title':title}
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
    def test_halloween_and_regular_nail_share_slot_without_overriding_business(self):
        a=self.add('ghost','Halloween ghost nail art'); b=self.add('https://www.sba.gov/counseling/plan-your-business/','How to build a nail salon'); c=self.add('marble','Luxury marble nails'); d=self.add('other','A short nail tutorial')
        self.assertEqual(set(r['id'] for r in candidates(self.store,'2026-10-29','12:00')),{a,c,d})
        self.assertEqual([r['id'] for r in candidates(self.store,'2026-10-29','16:00')],[b])
        self.assertNotIn(a,[r['id'] for r in candidates(self.store,'2026-10-29','20:00')])
    def test_published_halloween_no_longer_imposes_seven_day_cap(self):
        a=self.add('look','Halloween ghost nail art','A ghost for Halloween.')
        prior=self.add('pumpkin','Pumpkin nails');self.published(prior,'2026-10-24')
        for cap in (0,1):
            self.assertEqual([r['id'] for r in candidates(self.store,'2026-10-29','12:00',config={'topics':{'halloween_max_per_7_days':cap}})],[a])
    def test_tax_business_requires_official_source_and_keeps_untrusted_ready(self):
        aid=self.add('salon','Nail salon owner business guide','Review 1099 payroll and IRS tax obligations.')
        trusted=self.add('https://www.sba.gov/counseling/plan-your-business/','Nail salon owner business guide','Review 1099 payroll and IRS tax obligations.')
        self.assertEqual([row['id'] for row in candidates(self.store,'2026-10-29','16:00')],[trusted])
        self.assertEqual(self.store.db.execute('SELECT state FROM articles WHERE id=?',(aid,)).fetchone()[0],'ready')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM attempts').fetchone()[0],0)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM slots').fetchone()[0],0)
    def test_halloween_candidates_not_suppressed_by_regular_article(self):
        good=self.add('classy','Luxury nails')
        ghosts={self.add('ghost'+str(n),'Halloween ghost nails '+str(n)) for n in range(12)}
        self.assertEqual(set(r['id'] for r in candidates(self.store,'2026-10-29','12:00',limit=None)),ghosts|{good})
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
    def test_halloween_in_media_alt_does_not_suppress_article(self):
        elegant=self.add('elegant','Elegant manicure',alt='Halloween ghost nail')
        clean=self.add('classic','Classic nail design')
        self.assertEqual(set(r['id'] for r in candidates(self.store,'2026-10-29','12:00')),{clean,elegant})
    def test_legacy_halloween_option_has_no_effect_and_tax_policy_stays_strict(self):
        aid=self.add('ghost','Halloween ghost nails')
        self.assertEqual([r['id'] for r in candidates(self.store,'2026-10-29','12:00',config={'topics':{'halloween_max_per_7_days':0}})],[aid])
        self.assertEqual([r['id'] for r in candidates(self.store,'2026-10-29','12:00',config={'topics':{'us_tax_requires_human_review':False}})],[aid])
        for bad in ({'us_tax_requires_human_review':'yes'},{'halloween_max_per_7_days':2},{'halloween_max_per_7_days':True},{'unknown':1}):
            with self.subTest(bad=bad),self.assertRaises(ValueError):
                candidates(self.store,'2026-10-29','12:00',config={'topics':bad})


if __name__=='__main__':unittest.main()
