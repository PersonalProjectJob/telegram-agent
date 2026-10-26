"""Offline contract validation: malformed source cannot become a public form."""
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from agentfleet_console.errors import APIError
from agentfleet_console.registry import default_registry
from agentfleet_console.schema import validate_descriptor,validate_settings

class SchemaTests(unittest.TestCase):
    def setUp(self):self.raw=copy.deepcopy(default_registry().entries['triage']['descriptor'])
    def test_builtins_projection_and_defaults_are_valid(self):
        for entry in default_registry().entries.values():
            descriptor=validate_descriptor(entry['descriptor'])
            validate_settings(descriptor,{f['id']:f['default'] for f in descriptor['fields']})
            self.assertNotIn('mapping',descriptor)
            self.assertNotIn('/etc/',json.dumps(descriptor))
    def test_reject_bad_contract_and_unknown_condition_reference(self):
        mutations=[lambda d:d.update(schemaVersion=2),lambda d:d['fields'].append(copy.deepcopy(d['fields'][0])),lambda d:d['fields'][0].update(widget='html'),lambda d:d['fields'][0].update(group='missing'),lambda d:d['fields'][1].update(visibleWhen={'eval':'document.cookie'}),lambda d:d['fields'][1].update(disabledWhen={'equals':{'field':'secret','value':True}}),lambda d:d['groups'][0]['sections'][0].update(disabledWhen={'in':{'field':'provider','values':['unknown']}}),lambda d:d['fields'][0].update(default='unknown'),lambda d:d['fields'][1].update(default='a'*201),lambda d:d['fields'][1].update(maxLength=True),lambda d:d['fields'][1].update(minimum=float('nan')),lambda d:d['mapping'].pop('model'),lambda d:d['fields'][0]['options'].append(copy.deepcopy(d['fields'][0]['options'][0]))]
        for mutation in mutations:
            raw=copy.deepcopy(self.raw);mutation(raw)
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):validate_descriptor(raw)
    def test_complete_hidden_and_disabled_values_still_validate(self):
        public=validate_descriptor(self.raw);settings={f['id']:f['default'] for f in public['fields']}
        settings['fallback']='none';settings['fallbackThink']='false'
        with self.assertRaises(APIError) as error:validate_settings(public,settings)
        self.assertEqual(error.exception.field,'fallbackThink')
        settings['fallbackThink']=False;settings.pop('fallbackModel')
        with self.assertRaises(APIError):validate_settings(public,settings)
    def test_number_bounds_reject_bool_nonfinite_and_huge_integer(self):
        public=validate_descriptor(self.raw);settings={f['id']:f['default'] for f in public['fields']}
        for key,bad in [('timeout',True),('timeout',1.2),('timeout',1801),('confidence',float('nan')),('maxMB',float('inf')),('maxMB',10**400)]:
            with self.subTest(key=key,value=bad),self.assertRaises(APIError):validate_settings(public,{**settings,key:bad})
    def test_bounded_lists_and_condition_depth(self):
        field={'id':'tags','type':'list','label':'Tags','default':['a'],'itemType':'string','maxItems':2,'maxLength':3,'group':'ai','section':'limits','help':'','widget':'textarea'}
        self.raw['fields'].append(field);self.raw['mapping']['tags']={'section':'test','key':'tags','codec':'identity'}
        public=validate_descriptor(self.raw);settings={f['id']:f['default'] for f in public['fields']}
        for bad in (['a','b','c'],['long'],[{}],'a'):
            with self.subTest(value=bad),self.assertRaises(APIError):validate_settings(public,{**settings,'tags':bad})
        condition={'equals':{'field':'provider','value':'ollama'}}
        for _ in range(10):condition={'all':[condition]}
        self.raw['fields'][-1]['visibleWhen']=condition
        with self.assertRaises(ValueError):validate_descriptor(self.raw)
    def test_caps_and_size_limits(self):
        self.raw['description']='x'*300000
        with self.assertRaises(ValueError):validate_descriptor(self.raw)

if __name__=='__main__':unittest.main()
