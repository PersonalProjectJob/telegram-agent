import json
import copy
import unittest
from unittest.mock import patch
import test_official_auto_gate as fixture
from source_mirror.official_editorial_generator import generate,number_instructions,CDC_NAIL_WORKFORCE_SOURCE_SHA
from source_mirror.official_sources import SOURCES

class EditorialGeneratorTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.AutomaticGateTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.settings={'category':'Safety','published_at':'2026-11-01T08:00:00+07:00'}
        self.output={'locales':self.f.editorial['locales'],'hashtags':['nail','safety','work'],'uncertainties':[]}
    def call(self,output):
        with patch('source_mirror.official_editorial_generator.load_runtime_policy',return_value={'id':'trusted'}), \
             patch('source_mirror.official_editorial_generator.trusted_broker_call',return_value={'status':'SUCCESS','response':json.dumps(output)}) as broker:
            result=generate(self.f.root,SOURCES[0]['id'],self.f.sha,self.f.draft,self.settings)
            prompt=broker.call_args.args[1]
            self.assertIn('untrusted data',prompt)
            for cap in ('1-500 characters','1-70 characters','1-160 characters','2-50 characters'):
                self.assertIn(cap,prompt)
            for target in ('200-350 characters','at most 60 characters','at most 145 characters'):
                self.assertIn(target,prompt)
            self.assertIn('exact source digits and decimal/thousands separators',prompt)
            self.assertIn('never a current estimate',prompt)
            return result
    def test_generated_values_are_only_prospective(self):
        value=self.call(self.output)
        self.assertEqual(value['category'],'Safety');self.assertEqual(value['published_at'],self.settings['published_at'])
        self.assertFalse(value['is_featured']);self.assertEqual(value['video_urls'],[])
    def test_uncertainty_or_schema_change_is_held(self):
        for value in (dict(self.output,uncertainties=['ambiguous']),dict(self.output,extra='x')):
            with self.subTest(value=value),self.assertRaises(ValueError):self.call(value)
    def test_changed_source_is_held_before_broker(self):
        draft=json.loads(self.f.draft);draft['source']['reference_text']='changed'
        with patch('source_mirror.official_editorial_generator.trusted_broker_call') as broker,self.assertRaises(ValueError):
            generate(self.f.root,SOURCES[0]['id'],self.f.sha,json.dumps(draft).encode(),self.settings)
        broker.assert_not_called()

    def test_limits_apply_to_both_languages_without_repair(self):
        for language in ('vi','en'):
            for key,maximum in (('summary',500),('meta_title',70),('meta_description',160)):
                valid=copy.deepcopy(self.output);valid['locales'][language][key]='x'*maximum
                self.assertEqual(self.call(valid)['locales'][language][key],'x'*maximum)
                for bad in ('x'*(maximum+1),' text','text ','   '):
                    value=copy.deepcopy(self.output);value['locales'][language][key]=bad
                    with self.subTest(language=language,key=key,chars=len(bad)),self.assertRaises(ValueError):self.call(value)

    def test_hashtag_contract_matches_payload_and_preserves_valid_values(self):
        for tags in (['a','nail','work'],['x'*51,'nail','work'],['nail','nail','work'],
                     ['#nail','work','safe'],[' nail','work','safe'],['nail','work']):
            with self.subTest(tags=tags),self.assertRaises(ValueError):self.call(dict(self.output,hashtags=tags))
        tags=['ab','x'*50,'work']
        self.assertEqual(self.call(dict(self.output,hashtags=tags))['hashtags'],tags)

    def test_historical_numeric_note_requires_matching_source_evidence(self):
        reference='Over 400,000 workers. References: BLS 2017; BLS 2018.'
        note=number_instructions('cdc-niosh-nail-technicians',reference,CDC_NAIL_WORKFORCE_SOURCE_SHA)
        self.assertIn('cited 2017–2018 source material',note)
        self.assertIn('in that same summary or metadata field',note)
        self.assertIn('Otherwise omit that count',note)
        for digest in (None,'a'*64):
            self.assertNotIn('cited 2017–2018 source material',number_instructions('cdc-niosh-nail-technicians',reference,digest))
        for sid,text in [('sba-business-plan',reference),('cdc-niosh-nail-technicians','No historical count.'),
                         ('cdc-niosh-nail-technicians','400,000 workers in 2018.')]:
            self.assertNotIn('cited 2017–2018 source material',number_instructions(sid,text,CDC_NAIL_WORKFORCE_SOURCE_SHA))
