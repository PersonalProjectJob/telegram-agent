import copy
import json
import unittest

from source_mirror.agy_structured import schema,normalize
from source_mirror.localization import validate_review


class StructuredCodecTests(unittest.TestCase):
    def setUp(self):
        self.review={'verdict':'PASS','critical':0,'major':0,'minor':0,'issues':[],'uncertainties':[],
                     'glossary_ok':True,'accuracy_ok':True,'technical_clarity_ok':True}

    def envelope(self,value,response=None):
        return {'status':'SUCCESS','response':json.dumps(value) if response is None else response,
                'structured_output':value,'json_schema':schema('mqm-review')}

    def test_matching_single_fence_is_only_representation_normalization(self):
        raw='```json\n'+json.dumps(self.review)+'\n```'
        result=normalize(self.envelope(self.review,raw),'mqm-review')
        self.assertEqual(json.loads(result['response']),self.review)
        self.assertEqual(result['provider_response'],raw)
        self.assertEqual(result['original_envelope'],self.envelope(self.review,raw))
        self.assertIs(result['publication_enabled'],False)
        self.assertEqual(result['quality_status'],'NOT_EVALUATED_BY_CODEC')

    def test_real_cli_double_json_framing_requires_every_object_to_agree(self):
        pretty=json.dumps(self.review);compact=json.dumps(self.review,separators=(',',':'))
        for response in [pretty+'\n\n'+compact,'```json\n'+pretty+'\n```\n'+compact]:
            with self.subTest(response=response):
                result=normalize(self.envelope(self.review,response),'mqm-review')
                self.assertEqual(json.loads(result['response']),self.review)
                self.assertEqual(result['provider_response'],response)
        conflicting=json.dumps(dict(self.review,verdict='HOLD',uncertainties=['source warning']))
        with self.assertRaises(ValueError):normalize(self.envelope(self.review,pretty+'\n'+conflicting),'mqm-review')
        with self.assertRaises(ValueError):normalize(self.envelope(self.review,pretty+'\n{"verdict":"HOLD","verdict":"PASS"}'),'mqm-review')

    def test_uncertainty_and_hold_are_preserved_not_forced_to_pass(self):
        held=dict(self.review,verdict='HOLD',uncertainties=['Source is contradictory'],accuracy_ok=False)
        result=normalize(self.envelope(held,'```json\n'+json.dumps(held)+'\n```'),'mqm-review')
        self.assertEqual(json.loads(result['response']),held)
        self.assertEqual(schema('mqm-review')['properties']['verdict']['enum'],['PASS','HOLD'])
        self.assertNotIn('maxItems',schema('mqm-review')['properties']['uncertainties'])
        with self.assertRaises(ValueError):validate_review(json.loads(result['response']))

    def test_unknown_warning_and_codec_field_collisions_are_rejected(self):
        for key in ('warning','warnings','error','provider_response','original_envelope','publication_enabled','structured_schema_kind'):
            envelope=self.envelope(self.review);envelope[key]='source uncertainty remains'
            with self.subTest(key=key),self.assertRaises(ValueError):normalize(envelope,'mqm-review')
        envelope=self.envelope(self.review)
        envelope['usage']={'input_tokens':1,'output_tokens':1,'thinking_tokens':0,'cache_read_tokens':0,'total_tokens':2,'warning':'do not ignore'}
        with self.assertRaises(ValueError):normalize(envelope,'mqm-review')

    def test_conflicting_malformed_prose_and_duplicate_channels_fail_closed(self):
        for response in [json.dumps(dict(self.review,verdict='HOLD',uncertainties=['warning'])),
                         'Uncertainty remains.\n'+json.dumps(self.review),
                         '```json\n{"verdict":"HOLD","verdict":"PASS"}\n```',
                         '```json\n'+json.dumps(self.review)[:-1]+'\n```']:
            with self.subTest(response=response),self.assertRaises(ValueError):normalize(self.envelope(self.review,response),'mqm-review')

    def test_empty_generic_or_wrong_native_schema_never_passes(self):
        for change in [{'structured_output':{}},{'json_schema':{'type':'object'}},{'structured_output':None},
                       {'status':'ERROR'},{'error':'denied'},{'response':None}]:
            envelope=self.envelope(self.review);envelope.update(change)
            with self.subTest(change=change),self.assertRaises(ValueError):normalize(envelope,'mqm-review')
        invalid=copy.deepcopy(self.review);invalid['critical']=True
        with self.assertRaises(ValueError):normalize(self.envelope(invalid),'mqm-review')

    def test_official_candidate_uncertainty_remains_unchanged(self):
        candidate={'title_vi':'Bản thử','text_vi':'Nội dung tiếng Việt.','uncertainties':['Unclear term']}
        envelope={'status':'SUCCESS','response':'','structured_output':candidate,'json_schema':schema('official-draft')}
        self.assertEqual(json.loads(normalize(envelope,'official-draft')['response']),candidate)


if __name__=='__main__':unittest.main()
