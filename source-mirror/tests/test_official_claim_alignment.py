import copy
import base64
import hashlib
import json
import unittest
from source_mirror.official_claim_alignment import make_prompt,validate,digest_text
from source_mirror.official_sources import SOURCES,extract,source_title

class ClaimAlignmentTests(unittest.TestCase):
    def setUp(self):
        self.quote='The SBA provides guidance about tax planning for small businesses.'
        self.snapshot=self.snapshot_for(self.quote)
        self.draft={'candidate':{'title_vi':'Kế hoạch kinh doanh','text_vi':'Hướng dẫn lập kế hoạch thuế.'}}
        fields={'summary':'Hướng dẫn','meta_title':'Kế hoạch','meta_description':'Thông tin'}
        self.editorial={'locales':{'vi':fields,'en':fields},'category':'Business','hashtags':['nail','business','plan']}
        self.rights_quote='Photographer Example permits worldwide use of this stock image.'
        self.rights={'figure':self.rights_quote.encode()}
        self.media=[{'id':'figure','sha256':'b'*64,'alt':'Tools','credit':'Stock','rights_evidence_sha256':hashlib.sha256(self.rights['figure']).hexdigest()}]
        self.policy={'entries':[{'source_id':'sba-business-plan','source_url':self.snapshot['metadata']['source_url'],
            'geographic_scope':self.snapshot['metadata']['geographic_scope'],'image_sha256':'b'*64,'credit':'Stock','alt':'Tools',
            'rights_evidence_sha256':self.media[0]['rights_evidence_sha256'],'jurisdiction':'WORLDWIDE'}]}
        self.prompt,self.claims,_,_=make_prompt(self.snapshot,self.draft,self.editorial,self.media,self.rights,self.policy)
        self.review={'verdict':'PASS','uncertainties':[],'segments':[
            {'id':claim['id'],'segment_sha256':claim['sha256'],'evidence_key':claim['evidence_key'],
             'source_quote':self.quote,
             'grounded':True,'tax_scope_ok':True,'numbers_ok':True,'negation_ok':True,'names_ok':True} for claim in self.claims]}
    def snapshot_for(self,text):
        data=('<html><head><title>Business planning</title></head><body><main><p>'+text+'</p></main></body></html>').encode()
        item=next(x for x in SOURCES if x['id']=='sba-business-plan')
        return {'metadata':{'title':source_title(data),'source_id':item['id'],'source_url':item['url'],'geographic_scope':item['scope'],'source_sha256':hashlib.sha256(data).hexdigest()},
                'reference_text':extract(data),'source_html_base64':base64.b64encode(data).decode()}
    def check(self,review=None):
        return validate(self.snapshot,self.draft,self.editorial,self.media,self.rights,self.policy,{'status':'SUCCESS','response':json.dumps(review or self.review,ensure_ascii=False)})
    def test_all_fields_and_exact_citations_are_bound_without_publish_permission(self):
        result=self.check(); self.assertEqual(result['status'],'MODEL_ALIGNMENT_NOT_PUBLISHABLE')
        ids={item['id'] for item in result['segments']}
        self.assertTrue({'candidate.title_vi','candidate.text_vi.0','editorial.vi.summary','editorial.en.meta_title'}<=ids)
        self.assertEqual(result['media_policy_attestations'][0]['credit_sha256'],digest_text('Stock'))
        self.assertNotIn(self.rights_quote,self.prompt)
        self.assertEqual(result['prompt_sha256'],hashlib.sha256(self.prompt.encode()).hexdigest())
    def test_forged_quote_hash_missing_segment_or_wrong_verdict_are_held(self):
        for mutate in (lambda r:r['segments'].pop(),lambda r:r['segments'][0].update(source_quote='invented source quote not in snapshot'),
                       lambda r:r['segments'][0].update(segment_sha256='0'*64),
                       lambda r:r['segments'][0].update(grounded=False),lambda r:r.update(uncertainties=['unclear']),
                       lambda r:r['segments'].reverse()):
            review=copy.deepcopy(self.review);mutate(review)
            with self.subTest(review=review), self.assertRaises(ValueError):self.check(review)
        forged=copy.deepcopy(self.snapshot);forged['metadata']['source_id']='cdc-niosh-nail-technicians'
        with self.assertRaisesRegex(ValueError,'source identity'): validate(forged,self.draft,self.editorial,self.media,self.rights,self.policy,{'status':'SUCCESS','response':json.dumps(self.review,ensure_ascii=False)})
        forged=copy.deepcopy(self.snapshot);forged['source_html_base64']=base64.b64encode(b'<main><p>changed</p></main>').decode()
        with self.assertRaisesRegex(ValueError,'snapshot changed'): validate(forged,self.draft,self.editorial,self.media,self.rights,self.policy,{'status':'SUCCESS','response':json.dumps(self.review,ensure_ascii=False)})
        forged=copy.deepcopy(self.review);forged['segments'][-1]['evidence_key']='rights:figure'
        with self.assertRaisesRegex(ValueError,'coverage'):self.check(forged)
        with self.assertRaisesRegex(ValueError,'rights evidence changed'):validate(self.snapshot,self.draft,self.editorial,self.media,{'figure':b'changed'},self.policy, {'status':'SUCCESS','response':json.dumps(self.review,ensure_ascii=False)})
        forged_policy=copy.deepcopy(self.policy);forged_policy['entries'][0]['alt']='changed'
        with self.assertRaisesRegex(ValueError,'media policy mismatch'):validate(self.snapshot,self.draft,self.editorial,self.media,self.rights,forged_policy, {'status':'SUCCESS','response':json.dumps(self.review,ensure_ascii=False)})
    def test_added_number_negation_loss_and_ambiguous_quote_are_held(self):
        changed=copy.deepcopy(self.draft);changed['candidate']['text_vi']='Thuế 99%.'
        _,claims,_,_=make_prompt(self.snapshot,changed,self.editorial,self.media,self.rights,self.policy)
        review=copy.deepcopy(self.review);review['segments'][1]['segment_sha256']=claims[1]['sha256']
        with self.assertRaisesRegex(ValueError,'numbers'): validate(self.snapshot,changed,self.editorial,self.media,self.rights,self.policy,{'status':'SUCCESS','response':json.dumps(review,ensure_ascii=False)})
        quote='Do not assume that all small businesses are exempt.'
        snapshot=self.snapshot_for(quote)
        review=copy.deepcopy(self.review)
        for entry in review['segments']:
            if entry['evidence_key']=='source': entry.update(source_quote=quote)
        with self.assertRaisesRegex(ValueError,'negation'): validate(snapshot,self.draft,self.editorial,self.media,self.rights,self.policy,{'status':'SUCCESS','response':json.dumps(review,ensure_ascii=False)})
        snapshot=self.snapshot_for(self.quote+' '+self.quote)
        with self.assertRaisesRegex(ValueError,'ambiguous'):validate(snapshot,self.draft,self.editorial,self.media,self.rights,self.policy,{'status':'SUCCESS','response':json.dumps(self.review,ensure_ascii=False)})
