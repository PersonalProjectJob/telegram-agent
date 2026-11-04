import copy
import base64
import json
import io
import zipfile
import struct
import zlib
import sys
from pathlib import Path
from PIL import Image
from datetime import datetime, timezone
import unittest

from source_mirror.official_review_contract import VERSION, digest_bytes, digest_object, validate_contract
from source_mirror.official_sources import SOURCES
from source_mirror.official_draft import LABELS
from source_mirror.official_draft import GLOSSARY
from source_mirror.official_payload import build
from source_mirror.agy_broker import MODEL, EFFORT
from source_mirror.official_review_bundle import review_bundle


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.source=b'<html><head><title>Safety</title></head><body><main><p>Safety guidance.</p></main></body></html>'
        self.media={'figure-1':b'actual image'}; self.rights={'figure-1':b'rights evidence'}
        self.policy={'version':'1','owner_id':'owner-123','content_specialists':['safety-expert'],
                     'rights_specialists':['rights-expert'],'scope':'US workplace safety and media rights'}
        self.policy_sha=digest_object(self.policy); item=SOURCES[0]
        candidate={'title_vi':'An toàn làm việc','text_vi':'Hướng dẫn an toàn.','uncertainties':[]}
        review={'verdict':'PASS','critical':0,'major':0,'minor':0,'issues':[],'uncertainties':[],'glossary_ok':True,'accuracy_ok':True,'technical_clarity_ok':True}
        metadata={'source_id':item['id'],'source_url':item['url'],'category':item['category'],'geographic_scope':item['scope'],
                  'source_sha256':digest_bytes(self.source),'reference_text_sha256':digest_bytes(b'Safety guidance.\n'),
                  'title':'Safety','status':'expert_review_required','version':1}
        draft={'version':'official-review-v1','status':'HOLD','owner_approval':None,'specialist_review':None,
               'source':{'metadata':metadata,'reference_text':'Safety guidance.\n','source_label':LABELS[item['id']],'source_html_base64':base64.b64encode(self.source).decode()},
               'candidate':candidate,'automated_mqm':review,'generation_envelope':{'status':'SUCCESS','response':json.dumps(candidate,ensure_ascii=False)},
               'review_envelope':{'status':'SUCCESS','response':json.dumps(review)},'model':MODEL,'effort':EFFORT,'glossary':GLOSSARY,
               'generation_prompt_sha256':'a'*64,'review_prompt_sha256':'b'*64}
        self.draft=json.dumps(draft,ensure_ascii=False).encode('utf-8')
        media=[{'id':'figure-1','sha256':digest_bytes(self.media['figure-1']),
                'provenance_url':'https://www.cdc.gov/report.pdf','credit':'NIOSH', 'alt':'Figure 1',
                'rights_basis':'Evidence for independent review', 'rights_evidence_sha256':digest_bytes(self.rights['figure-1'])}]
        self.editorial={'locales':{'vi':{'summary':'An toàn','meta_title':'An toàn','meta_description':'Hướng dẫn'},
                                   'en':{'summary':'Safety','meta_title':'Safety','meta_description':'Guidance'}},
                        'hashtags':['nail','safety','work'],'category':'Safety','status':'Published','is_public':True,'is_featured':False,'video_urls':[],
                        'published_at':'2026-11-01T08:00:00+07:00'}
        self.payload=build(self.draft,media,self.editorial)
        self.contract={'version':VERSION,'source':{'id':item['id'],'url':item['url'],'label':LABELS[item['id']],'sha256':digest_bytes(self.source)},
                       'draft_sha256':digest_bytes(self.draft),'payload_sha256':digest_bytes(self.payload),
                       'policy_sha256':self.policy_sha,'media':media,'editorial':self.editorial,
                       'decisions':{'owner':'owner-proof','content_specialist':'content-proof','rights_specialist':'rights-proof'}}
        binding=digest_object({k:self.contract[k] for k in ('version','source','draft_sha256','payload_sha256','media','editorial','policy_sha256')})
        self.claims={role:{'subject':subject,'role':role,'binding_sha256':binding,'verdict':'approve',
                           'expires_at':'2027-01-01T00:00:00Z','scope':self.policy['scope']}
                     for role,subject in [('owner','owner-123'),('content_specialist','safety-expert'),('rights_specialist','rights-expert')]}
        self.now=datetime(2026,10,29,tzinfo=timezone.utc)

    def validate(self, **changes):
        kwargs={'source_bytes':self.source,'draft_bytes':self.draft,'payload_bytes':self.payload,'media_bytes':self.media,
                'rights_evidence_bytes':self.rights,'policy':self.policy,'trusted_policy_sha256':self.policy_sha,
                'authenticate':lambda role,proof:self.claims[role] if proof==self.contract['decisions'][role] else None,'now':self.now}
        kwargs.update(changes)
        return validate_contract(self.contract,**kwargs)

    def test_valid_test_proofs_still_cannot_publish(self):
        self.assertEqual(self.validate()['status'],'HOLD')

    def test_cdc_disclosure_and_attribution_are_in_both_locales(self):
        payload=json.loads(self.payload)
        vi=payload['locales']['vi']['body_html']; en=payload['locales']['en']['body_html']
        self.assertIn('Ảnh:',vi);self.assertIn('Image:',en)
        for text in (vi,en):
            self.assertIn('CDC/NIOSH',text); self.assertIn('HHS',text)
            self.assertIn('target="_blank"',text); self.assertIn('rel="noopener noreferrer"',text)
        self.assertIn('miễn phí',vi); self.assertIn('không hàm ý',vi)
        self.assertIn('free of charge',en); self.assertIn('do not imply endorsement',en)
        self.assertNotIn('individualized tax advice',en)

    def test_changed_source_draft_payload_media_or_evidence_rejected(self):
        for change in ({'source_bytes':b'changed'},{'draft_bytes':b'changed'},{'payload_bytes':b'changed'},
                       {'media_bytes':{'figure-1':b'changed'}},{'rights_evidence_bytes':{'figure-1':b'changed'}},
                       {'trusted_policy_sha256':'a'*64}):
            with self.subTest(change=change), self.assertRaises(ValueError): self.validate(**change)

    def test_tampered_source_attribution_or_media_binding_rejected(self):
        old=copy.deepcopy(self.contract)
        for mutate in (lambda c:c['source'].update(label='Scratch Magazine'),
                       lambda c:c['media'][0].update(credit='someone else'),
                       lambda c:c['source'].update(url='https://www.cdc.gov/other')):
            self.contract=copy.deepcopy(old); mutate(self.contract)
            with self.assertRaises(ValueError): self.validate()

    def test_missing_fake_stale_wrong_subject_and_scope_are_rejected(self):
        for role,key,value in [('owner','subject','attacker'),('content_specialist','subject','owner-123'),
                               ('rights_specialist','verdict','reject'),('owner','binding_sha256','a'*64),
                               ('owner','expires_at','2026-01-01T00:00:00Z'),('rights_specialist','scope','other')]:
            original=copy.deepcopy(self.claims); self.claims[role][key]=value
            with self.subTest(role=role,key=key), self.assertRaises(ValueError): self.validate()
            self.claims=original
        with self.assertRaises(ValueError): self.validate(authenticate=lambda *_: None)

    def test_rehashed_changed_payload_does_not_match_reviewed_draft(self):
        original=copy.deepcopy(self.contract)
        payload=json.loads(self.payload)
        for mutate in (lambda p:p['locales']['vi'].update(title='Tiêu đề giả'),
                       lambda p:p['locales']['vi'].update(body_html='<p>Thay đổi</p>'),
                       lambda p:p['locales']['vi'].update(summary='Mới'),
                       lambda p:p['locales']['en'].update(meta_title='Changed'),
                       lambda p:p['locales']['en'].update(meta_description='Changed'),
                       lambda p:p['source'].update(url='https://www.cdc.gov/changed'),
                       lambda p:p['media'][0].update(sha256='f'*64),
                       lambda p:p['media'][0].update(alt='Changed'),
                       lambda p:p['media'][0].update(credit='Changed'),
                       lambda p:p.update(category='Changed'),
                       lambda p:p.update(hashtags=['new','tags','here']),
                       lambda p:p.update(status='Draft'),
                       lambda p:p.update(is_public=False),
                       lambda p:p.update(is_featured=True),
                       lambda p:p.update(video_urls=['https://example.com/video']),
                       lambda p:p.update(published_at='2026-11-02T08:00:00+07:00')):
            value=copy.deepcopy(payload); mutate(value)
            changed=(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n').encode('utf-8')
            self.contract=copy.deepcopy(original); self.contract['payload_sha256']=digest_bytes(changed)
            with self.assertRaisesRegex(ValueError,'not canonical'): self.validate(payload_bytes=changed)

    def test_even_rehashed_new_media_requires_new_authenticated_decisions(self):
        self.media={'figure-1':b'new image'}
        self.contract['media'][0]['sha256']=digest_bytes(self.media['figure-1'])
        self.payload=build(self.draft,self.contract['media'],self.editorial)
        self.contract['payload_sha256']=digest_bytes(self.payload)
        with self.assertRaisesRegex(ValueError,'decision not valid'): self.validate()

    def test_deterministic_body_escapes_untrusted_markup(self):
        draft=json.loads(self.draft)
        draft['candidate']['text_vi']='Hướng dẫn <script>alert(x)</script> an toàn.'
        draft['generation_envelope']['response']=json.dumps(draft['candidate'],ensure_ascii=False)
        data=json.dumps(draft,ensure_ascii=False).encode('utf-8')
        first=build(data,self.contract['media'],self.editorial); self.assertEqual(first,build(data,self.contract['media'],self.editorial))
        result=json.loads(first)
        self.assertIn('&lt;script&gt;',result['locales']['vi']['body_html'])
        self.assertNotIn('<script>',result['locales']['vi']['body_html'])

    def test_editorial_is_required_and_changes_need_new_decisions(self):
        with self.assertRaises(ValueError): build(self.draft,self.contract['media'],{})
        editorial=copy.deepcopy(self.editorial); editorial['locales']['vi']['summary']='Bản tóm tắt mới'
        self.contract['editorial']=editorial
        self.payload=build(self.draft,self.contract['media'],editorial)
        self.contract['payload_sha256']=digest_bytes(self.payload)
        with self.assertRaisesRegex(ValueError,'decision not valid'): self.validate()

    def test_tax_in_editorial_remains_held(self):
        for locale,field,value in [('vi','summary','Hướng dẫn khai thuế'),('vi','meta_title','Thuế'),('en','meta_description','Tax reporting')]:
            editorial=copy.deepcopy(self.editorial); editorial['locales'][locale][field]=value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError,'tax remains held'):
                build(self.draft,self.contract['media'],editorial)
        editorial=copy.deepcopy(self.editorial); editorial['hashtags']=['nail','tax','safety']
        with self.assertRaisesRegex(ValueError,'tax remains held'): build(self.draft,self.contract['media'],editorial)
        for field in ('alt','credit'):
            media=copy.deepcopy(self.contract['media']); media[0][field]='Hướng dẫn thuế'
            with self.subTest(field=field), self.assertRaisesRegex(ValueError,'tax remains held'):
                build(self.draft,media,self.editorial)

    def test_owner_bundle_contains_exact_media_and_rights_with_reproducible_hash(self):
        output=io.BytesIO(); Image.new('RGB',(1,1),'white').save(output,format='PNG')
        image=output.getvalue(); media=copy.deepcopy(self.contract['media']); media[0]['sha256']=digest_bytes(image)
        result=review_bundle(self.draft,self.editorial,media,{'figure-1':image},self.rights)
        self.assertEqual(result['status'],'HOLD')
        self.assertEqual(result['bundle_sha256'],digest_bytes(result['bundle']))
        self.assertEqual(result,review_bundle(self.draft,self.editorial,media,{'figure-1':image},self.rights))
        with zipfile.ZipFile(io.BytesIO(result['bundle'])) as archive:
            self.assertEqual(archive.read('media/01.png'),image)
            self.assertEqual(archive.read('rights/01.txt'),self.rights['figure-1'])
            text=archive.read('owner-review.txt').decode('utf-8')
            self.assertIn('Hướng dẫn an toàn.',text); self.assertIn('Safety guidance.',text)
            self.assertIn('NIOSH',text); self.assertIn(media[0]['provenance_url'],text)
            self.assertEqual(digest_bytes(archive.read('payload.json')),result['payload_sha256'])
        with self.assertRaises(ValueError): review_bundle(self.draft,self.editorial,media,{'figure-1':b'changed'},self.rights)
        with self.assertRaises(ValueError): review_bundle(self.draft,self.editorial,media,{'figure-1':image},{'figure-1':b'forged rights'})

    def test_bundle_rejects_image_bombs_invalid_images_and_unsafe_evidence(self):
        def chunk(tag,data):
            return struct.pack('>I',len(data))+tag+data+struct.pack('>I',zlib.crc32(tag+data)&0xffffffff)
        ihdr=struct.pack('>IIBBBBB',12000,12000,8,2,0,0,0)
        bomb=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',ihdr)+chunk(b'IDAT',zlib.compress(b'x'))+chunk(b'IEND',b'')
        media=copy.deepcopy(self.contract['media'])
        for image in (bomb,b'not an image'):
            media[0]['sha256']=digest_bytes(image)
            with self.subTest(size=len(image)), self.assertRaises(ValueError):
                review_bundle(self.draft,self.editorial,media,{'figure-1':image},self.rights)
        output=io.BytesIO(); Image.new('RGB',(1,1),'white').save(output,format='PNG')
        image=output.getvalue(); media[0]['sha256']=digest_bytes(image)
        for evidence in (b'\xff\x00',b'unsafe\x00text',b'x'*256001):
            media[0]['rights_evidence_sha256']=digest_bytes(evidence)
            with self.subTest(size=len(evidence)), self.assertRaises(ValueError):
                review_bundle(self.draft,self.editorial,media,{'figure-1':image},{'figure-1':evidence})


if __name__=='__main__': unittest.main()
