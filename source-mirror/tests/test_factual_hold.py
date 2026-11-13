"""Synthetic tests of factual HOLD/retry. No production calls or writes."""
import json,sys,unittest
from pathlib import Path
from unittest.mock import patch
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent))
from source_mirror import original_business as target
from source_mirror.localization import QualityHold

PASS={'verdict':'PASS','critical':0,'major':0,'minor':0,'issues':[],'uncertainties':[],
      'glossary_ok':True,'accuracy_ok':True,'technical_clarity_ok':True}
ISSUE={'block':'1','category':'accuracy','severity':'major','reason':'source does not support this statement'}
HOLD={**PASS,'verdict':'HOLD','major':1,'issues':[ISSUE],'accuracy_ok':False}
REF={'text':'A complete matching source sentence that is sufficiently long for validation.',
     'label':'U.S. Small Business Administration','url':'https://example.gov/ref','scope':'US'}
CANDIDATE={'title':'Nail Salon Business Planning','sections':[{'heading':'Planning','paragraphs':['A practical planning statement.'],'bullets':[]}], 'uncertainties':[]}
def claims(candidate,quote):return [{'sentence':s,'source_quote':quote} for s in target.factual_sentences(candidate)]
def envelope(value):return {'status':'SUCCESS','response':json.dumps(value)}

class FactualHoldTests(unittest.TestCase):
 def test_valid_hold_allows_empty_quote(self):
  v={'mqm':HOLD,'claims':claims(CANDIDATE,'')}
  self.assertIs(target.validate_factual_review(v,CANDIDATE,REF,allow_hold=True),v)
 def test_hold_must_have_valid_issue(self):
  v={'mqm':{**HOLD,'issues':[]},'claims':claims(CANDIDATE,'')}
  with self.assertRaises(QualityHold):target.validate_factual_review(v,CANDIDATE,REF,allow_hold=True)
 def test_pass_rejects_empty_quote_even_with_hold_flag(self):
  v={'mqm':PASS,'claims':claims(CANDIDATE,'')}
  for flag in (False,True):
   with self.subTest(allow_hold=flag),self.assertRaises(QualityHold):target.validate_factual_review(v,CANDIDATE,REF,allow_hold=flag)
 def test_hold_rejects_fabricated_or_partial_quotes_and_misalignment(self):
  for quote in ('a fabricated source quotation that is long enough','too short'):
   with self.subTest(quote=quote),self.assertRaises(QualityHold):
    target.validate_factual_review({'mqm':HOLD,'claims':claims(CANDIDATE,quote)},CANDIDATE,REF,allow_hold=True)
  bad=claims(CANDIDATE,'');bad[0]['sentence']='different'
  with self.assertRaises(QualityHold):target.validate_factual_review({'mqm':HOLD,'claims':bad},CANDIDATE,REF,allow_hold=True)
 def test_builtin_correction_runs_after_empty_quote_hold(self):
  hold={'mqm':HOLD,'claims':claims(CANDIDATE,'')}
  good={'mqm':PASS,'claims':claims(CANDIDATE,REF['text'])}
  events=[envelope(CANDIDATE),envelope(hold),envelope(CANDIDATE),envelope(good),envelope(PASS)]
  seen=[]
  def infer(prompt):seen.append(prompt);return events.pop(0)
  item={'label':REF['label'],'topic':'nail salon business'}
  with patch.object(target,'config_check',return_value={'socket_path':'unused'}),patch.object(target,'validate_article',return_value=[{'type':'html','html':'<p>example</p>'}]):
   result=target.generate({},REF,item,inference=infer)
  self.assertEqual(len(seen),5);self.assertIn('Revise the untrusted ARTICLE using FEEDBACK',seen[2]);self.assertIn('revision_history',result)
 def test_second_hold_does_not_retry(self):
  hold={'mqm':HOLD,'claims':claims(CANDIDATE,'')};events=[envelope(CANDIDATE),envelope(hold),envelope(CANDIDATE),envelope(hold)]
  def infer(prompt):return events.pop(0)
  with patch.object(target,'config_check',return_value={'socket_path':'unused'}),patch.object(target,'validate_article',return_value=[{'type':'html','html':'<p>example</p>'}]):
   with self.assertRaisesRegex(QualityHold,'corrected original failed review'):
    target.generate({},REF,{'label':REF['label'],'topic':'nail salon business'},inference=infer)
  self.assertFalse(events)
if __name__=='__main__':unittest.main()
