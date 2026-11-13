import hashlib, json, tempfile, unittest
from pathlib import Path
from source_mirror.snapshot import save
from source_mirror.store import Store
from source_mirror.topics import candidates
from source_mirror.original_business import validate_article
from source_mirror.localization import QualityHold
from source_mirror.official_sources import SOURCES,collect
from source_mirror.official_draft import create
from source_mirror.official_payload import build
from tests.test_official_draft import CONFIG, REVIEW

class SourceAndTaxTests(unittest.TestCase):
 def test_business_source_is_independent_of_tax_keywords(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);store=Store(root)
   try:
    ids=[]
    for url,title,body in [
      ('https://www.sba.gov/counseling/plan-your-business/','Business plan tax strategy','Plan your nail salon tax registration.'),
      ('https://example.com/tax','Salon business tax strategy','Plan your nail salon tax registration.'),
      ('https://example.com/no-tax','Salon business guide','Plan your nail salon customer experience.'),
      ('https://example.com/nails','Halloween nail art','Paint your nail designs.')]:
     meta={'source_url':url,'title':title};save(root,meta,[{'type':'html','html':'<p>'+body+'</p>'}],'<p>source</p>',[]);ids.append(store.add_ready(url,title))
    self.assertEqual([x['id'] for x in candidates(store,'2026-11-05','16:00')],[ids[0]])
    self.assertEqual([x['id'] for x in candidates(store,'2026-11-05','12:00')],[ids[3]])
    self.assertEqual(store.db.execute('select state from articles where id=?',(ids[1],)).fetchone()[0],'ready')
   finally:store.close()
 def test_sba_tax_and_cash_register_are_not_prohibited_but_numeric_grounding_remains(self):
  candidate={'title':'Nail Salon Business Tax and Operations','sections':[{'heading':'Cash register and tax','paragraphs':['The nail salon business uses a cash register and plans tax registration carefully.'],'bullets':['Review the available guidance.','Record business information.']},{'heading':'Business planning','paragraphs':['A nail salon business reviews guidance relevant to its operations and location.'],'bullets':[]}], 'uncertainties':[]}
  ref={'label':'U.S. Small Business Administration','text':'tax register operations business'}
  self.assertEqual(len(validate_article(candidate,ref)),2)
  candidate['sections'][0]['paragraphs'][0]+=' File 123 forms.'
  with self.assertRaisesRegex(QualityHold,'numeric'):validate_article(candidate,ref)
 def test_official_irs_and_sba_tax_can_enter_draft_without_opt_in(self):
  for source in ('irs-tip-reporting','sba-business-plan'):
   with self.subTest(source=source),tempfile.TemporaryDirectory() as d:
    root=Path(d);item=next(x for x in SOURCES if x['id']==source)
    raw=b'<html><head><title>Tax guidance</title></head><body><main><p>Tax planning guidance.</p></main></body></html>'
    collect(root,item,lambda _:raw);values=iter([{'title_vi':'Hướng dẫn thuế','text_vi':'Hướng dẫn lập kế hoạch thuế.','uncertainties':[]},REVIEW]);draft=create(root,item['id'],hashlib.sha256(raw).hexdigest(),CONFIG,lambda _:{'status':'SUCCESS','response':json.dumps(next(values),ensure_ascii=False)})
    field={'summary':'Thông tin thuế','meta_title':'Kế hoạch thuế','meta_description':'Thông tin thuế'}
    ed={'locales':{'vi':field,'en':field},'hashtags':['nail','business','tax'],'category':'Business','status':'Published','is_public':True,'is_featured':False,'video_urls':[],'published_at':'2026-11-05T08:00:00+07:00'}
    media=[{'id':'x','sha256':'a'*64,'provenance_url':item['url'],'credit':'Official','rights_basis':'unverified','rights_evidence_sha256':'b'*64,'alt':'tax illustration'}]
    payload=json.loads(build(Path(draft['path']).read_bytes(),media,ed))
    self.assertEqual(payload['source']['id'],source)
    self.assertIn('tax',payload['locales']['en']['body_html'].lower())
if __name__=='__main__':unittest.main()

