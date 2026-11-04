import hashlib
import copy
import json
from pathlib import Path
import tempfile
import unittest

from source_mirror.official_sources import collect, SOURCES
from source_mirror.official_draft import create
from source_mirror.localization import QualityHold, MODEL, EFFORT
from source_mirror.official_payload import build

HTML = b'<html><head><title>Workplace safety</title></head><body><main><p>Workplace safety guidance.</p></main></body></html>'
CONFIG = {'localization': {'enabled': True, 'model': MODEL, 'effort': EFFORT, 'socket_path': '/unused'}}
CANDIDATE = {'title_vi': 'An toàn tại nơi làm việc', 'text_vi': 'Hướng dẫn an toàn tại nơi làm việc.', 'uncertainties': []}
REVIEW = {'verdict': 'PASS', 'critical': 0, 'major': 0, 'minor': 0, 'issues': [], 'uncertainties': [], 'glossary_ok': True, 'accuracy_ok': True, 'technical_clarity_ok': True}


class OfficialDraftTests(unittest.TestCase):
    def test_legacy_snapshot_stays_readable_without_rewriting_and_new_is_versioned(self):
        from source_mirror.official_draft import source_snapshot
        from source_mirror.official_sources import extract, collect_versioned
        raw=HTML.replace(b'Workplace safety guidance.',b'Female<span><a href="https://www.cdc.gov/a">1</a><a href="https://www.cdc.gov/b">2</a></span>; 12 workers.')
        sha=hashlib.sha256(raw).hexdigest(); item=SOURCES[0]
        folder=Path(collect(self.root,item,lambda _:raw)['snapshot'])
        self.assertEqual(source_snapshot(self.root,item['id'],sha)['metadata']['extractor_version'],2)
        legacy=extract(raw,citation_boundaries=False).encode()
        meta=json.loads((folder/'metadata.json').read_text(encoding='utf-8'))
        meta.pop('extractor_version');meta['reference_text_sha256']=hashlib.sha256(legacy).hexdigest()
        (folder/'reference.txt').write_bytes(legacy)
        (folder/'metadata.json').write_text(json.dumps(meta),encoding='utf-8')
        before={p.name:p.read_bytes() for p in folder.iterdir()}
        self.assertIn('Female12',source_snapshot(self.root,item['id'],sha)['reference_text'])
        self.assertTrue(collect(self.root,item,lambda _:raw)['unchanged'])
        self.assertEqual(before,{p.name:p.read_bytes() for p in folder.iterdir()})
        with self.assertRaisesRegex(QualityHold,'legacy source extraction'):
            create(self.root,item['id'],sha,CONFIG,lambda _:self.fail('legacy snapshot reached inference'))
        new=collect_versioned(self.root,item,lambda _:raw)
        self.assertNotEqual(Path(new['snapshot']),folder)
        self.assertIn('Female1 2',source_snapshot(new['root'],item['id'],sha)['reference_text'])
        self.assertEqual(before,{p.name:p.read_bytes() for p in folder.iterdir()})
        meta['extractor_version']=2;(folder/'metadata.json').write_text(json.dumps(meta),encoding='utf-8')
        with self.assertRaises(QualityHold): source_snapshot(self.root,item['id'],sha)
        with self.assertRaises(ValueError): collect(self.root,item,lambda _:raw)
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.snapshot = Path(collect(self.root, SOURCES[0], lambda _: HTML)['snapshot'])
        self.sha = hashlib.sha256(HTML).hexdigest()

    def generate(self, outputs=None):
        outputs = iter(outputs or [CANDIDATE, REVIEW])
        return create(self.root, SOURCES[0]['id'], self.sha, CONFIG,
                      lambda _: {'status': 'SUCCESS', 'response': json.dumps(next(outputs), ensure_ascii=False)})

    def test_artifact_is_held_bound_and_replay_idempotent(self):
        result = self.generate(); path = Path(result['path']); artifact = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), result['sha256'])
        self.assertEqual(result, self.generate())
        self.assertEqual(artifact['status'], 'HOLD'); self.assertIsNone(artifact['owner_approval']); self.assertIsNone(artifact['specialist_review'])
        self.assertEqual(artifact['source']['source_label'], 'CDC/NIOSH')
        self.assertFalse((self.root / 'articles').exists())
        path.write_text('tampered')
        with self.assertRaises(QualityHold): self.generate()

    def test_changed_source_and_uncertain_or_failing_review_rejected(self):
        (self.snapshot / 'reference.txt').write_text('changed')
        with self.assertRaises(QualityHold): self.generate()
        (self.snapshot / 'reference.txt').write_text('Workplace safety guidance.\n', encoding='utf-8')
        for outputs in ([dict(CANDIDATE, uncertainties=['unclear'])], [CANDIDATE, dict(REVIEW, major=1)],
                        [dict(CANDIDATE, text_vi='Hướng dẫn khai thuế'), REVIEW]):
            with self.subTest(outputs=outputs), self.assertRaises(QualityHold): self.generate(outputs)
        with self.assertRaises(Exception):
            create(self.root, SOURCES[0]['id'], self.sha, CONFIG, lambda _: {'status': 'ERROR'})

    def test_tax_source_never_inferred(self):
        data = HTML.replace(b'Workplace safety guidance.', b'Tax reporting guidance.')
        collect(self.root, SOURCES[0], lambda _: data)
        with self.assertRaisesRegex(QualityHold, 'tax topic'):
            create(self.root, SOURCES[0]['id'], hashlib.sha256(data).hexdigest(), CONFIG, lambda _: self.fail('inference called'))
        # Entire IRS categories stay held even when keywords are absent.
        for item in (x for x in SOURCES if x['category']=='us-tax'):
            collect(self.root,item,lambda _: HTML)
            with self.subTest(source=item['id']), self.assertRaisesRegex(QualityHold,'tax topic'):
                create(self.root,item['id'],self.sha,CONFIG,lambda _: self.fail('inference called'))

    def test_sba_incidental_tax_requires_opt_in_and_keeps_source_specific_scope(self):
        item=next(x for x in SOURCES if x['id']=='sba-business-plan')
        data=b'<html><head><title>Business planning</title></head><body><main><p>Tax planning guidance.</p></main></body></html>'
        collect(self.root,item,lambda _:data); sha=hashlib.sha256(data).hexdigest()
        with self.assertRaisesRegex(QualityHold,'tax topic'):
            create(self.root,item['id'],sha,CONFIG,lambda _:self.fail('tax requires opt in'))
        candidate={'title_vi':'Kế hoạch kinh doanh','text_vi':'Hướng dẫn lập kế hoạch thuế.','uncertainties':[]}
        outputs=iter([candidate,REVIEW])
        result=create(self.root,item['id'],sha,CONFIG,lambda _: {'status':'SUCCESS','response':json.dumps(next(outputs),ensure_ascii=False)},allow_official_tax=True)
        draft=Path(result['path']).read_bytes()
        field={'summary':'Thông tin','meta_title':'Kế hoạch','meta_description':'Thông tin chung'}
        editorial={'locales':{'vi':field,'en':field},'hashtags':['nail','business','plan'],'category':'Safety','status':'Published','is_public':True,'is_featured':False,'video_urls':[],'published_at':'2026-11-01T08:00:00+07:00'}
        media=[{'id':'x','sha256':'a'*64,'provenance_url':'https://www.sba.gov/x','credit':'SBA','rights_basis':'unverified','rights_evidence_sha256':'b'*64,'alt':'illustration'}]
        with self.assertRaisesRegex(ValueError,'tax remains held'): build(draft,media,editorial)
        payload=json.loads(build(draft,media,editorial,allow_official_tax=True))
        self.assertIn('not individualized tax advice',payload['locales']['en']['body_html'])
        self.assertIn('federal, state, and local',payload['locales']['en']['body_html'])
        self.assertEqual(payload['source']['scope'],item['scope'])
        false_candidate=dict(candidate,text_vi='Chủ tiệm được miễn mọi khoản thuế.')
        calls=[]
        def false_pass(_):
            calls.append(1); return {'status':'SUCCESS','response':json.dumps(false_candidate,ensure_ascii=False)}
        with self.assertRaisesRegex(QualityHold,'blanket tax exemption'):
            create(self.root,item['id'],sha,CONFIG,false_pass,allow_official_tax=True)
        self.assertEqual(len(calls),1)
        for field in ('summary','meta_title','meta_description'):
            altered=copy.deepcopy(editorial); altered['locales']['vi'][field]='Chủ tiệm được miễn mọi khoản thuế'
            with self.subTest(field=field), self.assertRaisesRegex(ValueError,'blanket tax'):
                build(draft,media,altered,allow_official_tax=True)
        altered=copy.deepcopy(media); altered[0]['alt']='Chủ tiệm được miễn mọi khoản thuế'
        with self.assertRaisesRegex(ValueError,'blanket tax'): build(draft,altered,editorial,allow_official_tax=True)
        non_sba=SOURCES[0]; collect(self.root,non_sba,lambda _:data)
        with self.assertRaisesRegex(QualityHold,'tax topic'):
            create(self.root,non_sba['id'],sha,CONFIG,lambda _:self.fail('other source tax held'),allow_official_tax=True)

    def test_english_fields_rejected_even_if_mqm_would_pass(self):
        for field, english in [('title_vi', 'Workplace safety'), ('text_vi', 'Workplace safety guidance.')]:
            with self.subTest(field=field), self.assertRaisesRegex(QualityHold, 'Vietnamese translation missing'):
                self.generate([dict(CANDIDATE, **{field: english}), REVIEW])

    def test_numbers_and_negation_fail_before_mqm(self):
        for english, vietnamese, error in [
            (b'Use 2 filters.', 'Dùng 3 bộ lọc.', 'numeric facts'),
            (b'Do not inhale dust.', 'Hít bụi.', 'negation'),
        ]:
            data = HTML.replace(b'Workplace safety guidance.', english)
            collect(self.root, SOURCES[0], lambda _: data)
            sha = hashlib.sha256(data).hexdigest()
            candidate = dict(CANDIDATE, text_vi=vietnamese)
            calls = []
            def inference(_):
                calls.append(1)
                return {'status': 'SUCCESS', 'response': json.dumps(candidate, ensure_ascii=False)}
            with self.subTest(english=english), self.assertRaisesRegex(QualityHold, error):
                create(self.root, SOURCES[0]['id'], sha, CONFIG, inference)
            self.assertEqual(len(calls), 1)

    def test_generation_hold_records_private_diagnostic_without_envelope_metadata(self):
        response = json.dumps(dict(CANDIDATE, uncertainties=['needs expert']), ensure_ascii=False)
        with self.assertRaises(QualityHold):
            create(self.root, SOURCES[0]['id'], self.sha, CONFIG,
                   lambda _: {'status': 'SUCCESS', 'response': response, 'secret_metadata': 'do not store'})
        paths = list((self.root/'official-holds'/SOURCES[0]['id']/self.sha).glob('*.json'))
        self.assertEqual(len(paths), 1)
        evidence = json.loads(paths[0].read_text(encoding='utf-8'))
        self.assertEqual(evidence['reason'], 'generator_uncertainty')
        self.assertEqual(evidence['response'], response)
        self.assertNotIn('secret_metadata', evidence)

    def test_validation_and_review_holds_save_only_private_bounded_response(self):
        cases=[(HTML,dict(CANDIDATE,text_vi='English only'),None,'translation_validation_hold'),
               (HTML.replace(b'Workplace safety guidance.',b'Read <a href="https://www.cdc.gov/guidance">guidance</a>.'),CANDIDATE,None,'translation_validation_hold'),
               (HTML.replace(b'Workplace safety guidance.',b'Use 2 filters.'),dict(CANDIDATE,text_vi='Dùng 3 bộ lọc.'),None,'translation_validation_hold'),
               (HTML,CANDIDATE,dict(REVIEW,verdict='HOLD',uncertainties=['unclear']),'mqm_validation_hold')]
        for index,(raw,candidate,review,reason) in enumerate(cases):
            with self.subTest(index=index), tempfile.TemporaryDirectory() as directory:
                root=Path(directory); item=SOURCES[0]; collect(root,item,lambda _:raw)
                sha=hashlib.sha256(raw).hexdigest(); outputs=iter([candidate,review]);calls=[]
                def infer(_):
                    calls.append(1)
                    return {'status':'SUCCESS','response':json.dumps(next(outputs),ensure_ascii=False),'secret_metadata':'SENTINEL'}
                with self.assertRaises(QualityHold): create(root,item['id'],sha,CONFIG,infer)
                paths=list((root/'official-holds'/item['id']/sha).glob('*.json'))
                self.assertEqual(len(paths),1)
                evidence=json.loads(paths[0].read_text(encoding='utf-8'))
                self.assertEqual(evidence['reason'],reason)
                self.assertEqual(evidence['stage'],'review' if review else 'generation')
                self.assertEqual(json.loads(evidence['response']),review if review else candidate)
                self.assertNotIn('SENTINEL',paths[0].read_text(encoding='utf-8'))
                self.assertEqual(len(calls),2 if review else 1)

    def test_hold_evidence_io_failure_and_symlink_fail_closed(self):
        from unittest.mock import patch
        response=json.dumps(dict(CANDIDATE,uncertainties=['unclear']))
        infer=lambda _: {'status':'SUCCESS','response':response}
        with patch('source_mirror.official_draft.os.open',side_effect=OSError('SENTINEL secret')):
            with self.assertRaisesRegex(QualityHold,'generation/generator_uncertainty: private hold evidence unavailable') as caught:
                create(self.root,SOURCES[0]['id'],self.sha,CONFIG,infer)
        self.assertNotIn('SENTINEL',str(caught.exception))
        outputs=iter([CANDIDATE,dict(REVIEW,verdict='HOLD',uncertainties=['unclear'])])
        with patch('source_mirror.official_draft.os.open',side_effect=OSError('SENTINEL secret')):
            with self.assertRaisesRegex(QualityHold,'review/mqm_validation_hold: private hold evidence unavailable') as caught:
                create(self.root,SOURCES[0]['id'],self.sha,CONFIG,
                       lambda _: {'status':'SUCCESS','response':json.dumps(next(outputs),ensure_ascii=False)})
        self.assertNotIn('SENTINEL',str(caught.exception))
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);collect(root,SOURCES[0],lambda _:HTML)
            outside=root/'outside';outside.mkdir()
            try: (root/'official-holds').symlink_to(outside,target_is_directory=True)
            except (OSError,NotImplementedError): return
            with self.assertRaisesRegex(QualityHold,'private hold evidence unavailable'):
                create(root,SOURCES[0]['id'],self.sha,CONFIG,infer)
            self.assertEqual(list(outside.iterdir()),[])

    def test_pass_artifact_excludes_model_envelope_metadata(self):
        outputs = iter([CANDIDATE, REVIEW])
        result = create(self.root, SOURCES[0]['id'], self.sha, CONFIG,
                        lambda _: {'status': 'SUCCESS', 'response': json.dumps(next(outputs), ensure_ascii=False), 'secret_metadata': 'SENTINEL-SECRET'})
        self.assertNotIn('SENTINEL-SECRET', Path(result['path']).read_text(encoding='utf-8'))

    def test_generation_and_review_require_direct_final_json_without_tools(self):
        outputs=iter([CANDIDATE,REVIEW]);prompts=[]
        def infer(prompt):
            prompts.append(prompt)
            return {'status':'SUCCESS','response':json.dumps(next(outputs),ensure_ascii=False)}
        result=create(self.root,SOURCES[0]['id'],self.sha,CONFIG,infer)
        self.assertEqual(result['status'],'HOLD')
        self.assertEqual(len(prompts),2)
        for prompt in prompts:
            controls=prompt.split('GLOSSARY=',1)[0]
            self.assertIn('Do not call tools, execute commands, access the network, or read/write files.',controls)
            self.assertIn('Do not run Python or use a terminal',controls)
            self.assertIn('directly in your final response, not as a tool call or file',controls)
            self.assertIn('untrusted data',controls)

    def test_retained_original_terms_and_general_scope_do_not_require_invented_advice(self):
        outputs=iter([CANDIDATE,REVIEW]);prompts=[]
        def infer(prompt):
            prompts.append(prompt)
            return {'status':'SUCCESS','response':json.dumps(next(outputs),ensure_ascii=False)}
        result=create(self.root,SOURCES[0]['id'],self.sha,CONFIG,infer)
        self.assertEqual(result['status'],'HOLD')
        self.assertEqual(len(prompts),2)
        for prompt in prompts:
            controls=prompt.split('GLOSSARY=',1)[0]
            self.assertIn('jobber or statutory employee',controls)
            self.assertIn('no reliable Vietnamese equivalent',controls)
            self.assertIn('particular nail salon',controls)
            self.assertIn('individual salon may differ',controls)
            self.assertIn('conditions and qualifications',controls)
        self.assertIn('Never suppress an actual uncertainty',prompts[0])
        self.assertIn('Still flag substantive ambiguity or contradiction',prompts[1])

    def test_harmless_source_grammar_and_federal_scope_are_informational_not_new_claims(self):
        source=HTML.replace(b'Workplace safety guidance.',b'Notices 2025-62 and 2025-69 and provide penalty relief under federal law. State rules may differ.')
        item=next(item for item in SOURCES if item['id']=='irs-tip-reporting')
        collect(self.root,item,lambda _:source);sha=hashlib.sha256(source).hexdigest()
        candidate=dict(CANDIDATE,text_vi='Các Thông báo 2025-62 và 2025-69 đưa ra việc giảm nhẹ tiền phạt theo luật liên bang. Quy định của tiểu bang có thể khác.')
        outputs=iter([candidate,REVIEW]);prompts=[]
        def infer(prompt):
            prompts.append(prompt);return {'status':'SUCCESS','response':json.dumps(next(outputs),ensure_ascii=False)}
        result=create(self.root,item['id'],sha,CONFIG,infer,allow_official_tax=True)
        artifact=json.loads(Path(result['path']).read_text(encoding='utf-8'))
        self.assertEqual(artifact['candidate'],candidate)
        for prompt in prompts:
            controls=prompt.split('GLOSSARY=',1)[0]
            self.assertIn('and ... and provide',controls)
            self.assertIn('actors, action, objects and conditions',controls)
            self.assertIn('informational',controls)
            self.assertIn('blanket state/local',controls)
            self.assertIn('disclosure',controls)
        self.assertIn('do not add a notes field',prompts[0])

    def test_numeric_surface_forms_are_prompted_without_relaxing_numeric_gate(self):
        source=HTML.replace(b'Workplace safety guidance.',
                            b'Cash tips received in August are reported by September 10. Before December 31, use a rate of 0.9% and an amount greater than zero. Beginning January 1st, records apply.')
        item=next(item for item in SOURCES if item['id']=='irs-tip-reporting')
        collect(self.root,item,lambda _:source);sha=hashlib.sha256(source).hexdigest()
        candidate=dict(CANDIDATE,text_vi='Tiền tip bằng tiền mặt nhận được trong tháng Tám được báo cáo trước ngày 10 tháng Chín. Trước ngày 31 tháng Mười Hai, dùng tỷ lệ 0.9% và số tiền lớn hơn không. Bắt đầu ngày 1 tháng Một, hồ sơ được áp dụng.')
        outputs=iter([candidate,REVIEW]);prompts=[]
        def infer(prompt):
            prompts.append(prompt);return {'status':'SUCCESS','response':json.dumps(next(outputs),ensure_ascii=False)}
        result=create(self.root,item['id'],sha,CONFIG,infer,allow_official_tax=True)
        artifact=json.loads(Path(result['path']).read_text(encoding='utf-8'))
        self.assertEqual(artifact['candidate'],candidate)
        self.assertEqual(len(prompts),2)
        for prompt in prompts:
            controls=prompt.split('GLOSSARY=',1)[0].lower()
            self.assertIn('numeric tokens exactly as written',controls)
            self.assertIn('decimal/thousands separators and occurrence counts',controls)
        self.assertIn('August -> tháng Tám',prompts[0])
        self.assertIn('January -> tháng Một, zero -> không',prompts[0])
        self.assertIn('Changed values, units, dates, reference numbers',prompts[1])
        for changed in [candidate['text_vi'].replace('0.9%','0,9%'),
                        candidate['text_vi'].replace('tháng Tám','tháng 8'),
                        candidate['text_vi'].replace('0.9%','0.8%')]:
            calls=[]
            def rejected(prompt):
                calls.append(prompt);return {'status':'SUCCESS','response':json.dumps(dict(candidate,text_vi=changed),ensure_ascii=False)}
            with self.subTest(changed=changed),self.assertRaisesRegex(QualityHold,'numeric facts'):
                create(self.root,item['id'],sha,CONFIG,rejected,allow_official_tax=True)
            self.assertEqual(len(calls),1)

    def test_observed_mixed_english_defects_hold_create_and_external_payload(self):
        from source_mirror.official_payload import build
        source=(b'<html><head><title>Tip reporting</title></head><body><main><p>Report by September 10th at 8 percent. Read '
                b'<a href="https://www.irs.gov/transition">transition relief for tax year 2025 related to the new information reporting requirements</a>, '
                b'<a href="https://www.irs.gov/credit">the Federal Insurance Contributions Act (FICA) tip credit</a>, '
                b'<a href="https://www.irs.gov/pub531">Publication 531, Reporting Tip Income</a>, '
                b'<a href="https://www.irs.gov/agency">Internal Revenue Service (IRS)</a> and '
                b'<a href="https://www.irs.gov/law">Federal Insurance Contributions Act (FICA)</a>.</p></main></body></html>')
        item=next(item for item in SOURCES if item['id']=='irs-tip-reporting')
        collect(self.root,item,lambda _:source);sha=hashlib.sha256(source).hexdigest()
        translated=('Báo cáo trước ngày 10 tháng Chín với tỷ lệ 8 phần trăm. Đọc '
                    '⟦A⟧biện pháp giảm nhẹ chuyển tiếp cho năm thuế 2025 liên quan đến các yêu cầu báo cáo thông tin mới⟦/A⟧, '
                    '⟦B⟧khoản tín dụng thuế tiền tip theo Federal Insurance Contributions Act (FICA)⟦/B⟧, '
                    '⟦C⟧Publication 531, Reporting Tip Income⟦/C⟧, '
                    '⟦D⟧Internal Revenue Service (IRS)⟦/D⟧ và ⟦E⟧Federal Insurance Contributions Act (FICA)⟦/E⟧.')
        candidate=dict(CANDIDATE,title_vi='Báo cáo tiền tip',text_vi=translated)
        outputs=iter([candidate,REVIEW]);prompts=[]
        def infer(prompt):
            prompts.append(prompt);return {'status':'SUCCESS','response':json.dumps(next(outputs),ensure_ascii=False)}
        result=create(self.root,item['id'],sha,CONFIG,infer,allow_official_tax=True)
        original=Path(result['path']).read_bytes()
        self.assertIn('English ordinal suffixes st/nd/rd/th are grammar',prompts[0])
        self.assertIn('A linked phrase is not automatically a publication title',prompts[0])
        self.assertIn('Reject untranslated percent units and descriptive English anchor sentences',prompts[1])
        media=[{'id':'figure','sha256':'0'*64,'provenance_url':'https://www.irs.gov/figure.png','credit':'IRS',
                'rights_basis':'Source evidence pending','rights_evidence_sha256':'1'*64,'alt':'Hình minh họa'}]
        editorial={'locales':{'vi':{'summary':'Báo cáo tiền tip','meta_title':'Báo cáo tiền tip','meta_description':'Nguồn chính thức'},
                             'en':{'summary':'Tip reporting','meta_title':'Tip reporting','meta_description':'Official source'}},
                   'hashtags':['business','tips','irs'],'category':'Business','status':'Published','is_public':True,
                   'is_featured':False,'video_urls':[],'published_at':'2030-01-01T08:00:00+07:00'}
        self.assertIsInstance(build(original,media,editorial,allow_official_tax=True),bytes)
        english_anchor='transition relief for tax year 2025 related to the new information reporting requirements'
        original_vi='biện pháp giảm nhẹ chuyển tiếp cho năm thuế 2025 liên quan đến các yêu cầu báo cáo thông tin mới'
        bad_texts=[translated.replace('ngày 10 tháng','ngày 10th tháng'),
                   translated.replace('ngày 10 tháng','ngày 10 th tháng'),
                   translated.replace('8 phần trăm','8 percent'),
                   translated.replace(original_vi,english_anchor),
                   translated.replace(original_vi,'Thông tin '+english_anchor),
                   translated.replace('khoản tín dụng thuế tiền tip theo Federal Insurance Contributions Act (FICA)',
                                      'the Federal Insurance Contributions Act (FICA) tip credit')]
        for text in bad_texts:
            bad=dict(candidate,text_vi=text)
            with self.subTest(text=text),self.assertRaisesRegex(QualityHold,'date|percentage|descriptive'):
                create(self.root,item['id'],sha,CONFIG,lambda _: {'status':'SUCCESS','response':json.dumps(bad,ensure_ascii=False)},allow_official_tax=True)
            artifact=json.loads(original);artifact['candidate']=bad
            artifact['generation_envelope']['response']=json.dumps(bad,ensure_ascii=False)
            with self.subTest(external=text),self.assertRaisesRegex(ValueError,'date|percentage|descriptive'):
                build(json.dumps(artifact,ensure_ascii=False).encode(),media,editorial,allow_official_tax=True)

    def test_surface_guard_allows_exact_source_document_names_with_english_units(self):
        from source_mirror.official_draft import validate_vietnamese_surface
        label='Publication 123, An 8 Percent Example'
        source={'metadata':{'title':'Document reference'},'reference_text':label}
        candidate={'title_vi':'Tài liệu tham khảo','text_vi':'Xem ⟦A⟧'+label+'⟦/A⟧.'}
        validate_vietnamese_surface(source,candidate,[{'text':label}])

    def test_grammar_guidance_does_not_remove_substantive_uncertainty_or_scope_errors(self):
        source=HTML.replace(b'Workplace safety guidance.',b'Federal penalty relief applies under the stated conditions. State rules may differ.')
        item=next(item for item in SOURCES if item['id']=='irs-tip-reporting')
        collect(self.root,item,lambda _:source);sha=hashlib.sha256(source).hexdigest()
        faithful=dict(CANDIDATE,text_vi='Việc giảm nhẹ tiền phạt liên bang áp dụng theo các điều kiện đã nêu. Quy định của tiểu bang có thể khác.')
        changed=dict(CANDIDATE,text_vi='Mọi chủ tiệm đều được miễn mọi khoản thuế liên bang và tiểu bang.')
        major=dict(REVIEW,major=1,issues=[{'block':'text_vi','category':'Accuracy','severity':'major','reason':'Changes conditional federal relief into universal state exemption'}])
        for outputs in [[dict(faithful,uncertainties=['Source dates contradict one another'])],
                        [faithful,dict(REVIEW,uncertainties=['Actor or relief condition remains unresolved'])],
                        [changed,major]]:
            calls=[];values=iter(outputs)
            def infer(prompt):
                calls.append(prompt);return {'status':'SUCCESS','response':json.dumps(next(values),ensure_ascii=False)}
            with self.subTest(outputs=outputs),self.assertRaises(QualityHold):
                create(self.root,item['id'],sha,CONFIG,infer,allow_official_tax=True)
        # A superficially harmless grammar observation never clears a flag in
        # model output; the application still rejects every nonempty array.
        values=iter([dict(faithful,uncertainties=['Repeated conjunction observed'])])
        with self.assertRaises(QualityHold):
            create(self.root,item['id'],sha,CONFIG,lambda _: {'status':'SUCCESS','response':json.dumps(next(values),ensure_ascii=False)},allow_official_tax=True)

    def test_retained_term_does_not_clear_real_uncertainty_or_review_error(self):
        source=HTML.replace(b'Workplace safety guidance.',b'A jobber and a statutory employee are discussed under the stated conditions.')
        item=next(item for item in SOURCES if item['id']=='irs-worker-classification')
        collect(self.root,item,lambda _:source);sha=hashlib.sha256(source).hexdigest()
        candidate=dict(CANDIDATE,text_vi='Nguồn đề cập jobber và statutory employee theo các điều kiện đã nêu.')
        for outputs,expected_calls in [([dict(candidate,uncertainties=['source conflicts about eligibility'])],1),
                                       ([candidate,dict(REVIEW,uncertainties=['source contradicts itself'])],2),
                                       ([candidate,dict(REVIEW,major=1)],2)]:
            calls=[];values=iter(outputs)
            def infer(prompt):
                calls.append(prompt);return {'status':'SUCCESS','response':json.dumps(next(values),ensure_ascii=False)}
            with self.subTest(outputs=outputs),self.assertRaises(QualityHold):
                create(self.root,item['id'],sha,CONFIG,infer,allow_official_tax=True)
            self.assertEqual(len(calls),expected_calls)

    def test_retained_legal_labels_bind_actual_source_before_mqm(self):
        source=HTML.replace(b'Workplace safety guidance.',b'A jobber and a statutory employee are discussed under the stated conditions.')
        item=next(item for item in SOURCES if item['id']=='irs-worker-classification')
        collect(self.root,item,lambda _:source);sha=hashlib.sha256(source).hexdigest()
        candidate=dict(CANDIDATE,text_vi='Nguồn đề cập jobber và statutory employee theo các điều kiện đã nêu.')
        outputs=iter([candidate,REVIEW]);calls=[]
        def infer(prompt):
            calls.append(prompt);return {'status':'SUCCESS','response':json.dumps(next(outputs),ensure_ascii=False)}
        result=create(self.root,item['id'],sha,CONFIG,infer,allow_official_tax=True)
        artifact=json.loads(Path(result['path']).read_text(encoding='utf-8'))
        self.assertEqual(artifact['candidate']['text_vi'],candidate['text_vi'])
        self.assertEqual(len(calls),2)
        for changed in [candidate['text_vi'].replace('jobber và ',''),
                        candidate['text_vi'].replace('statutory employee','statutory contractor'),
                        candidate['text_vi'].replace('jobber','jobbers'),
                        candidate['text_vi']+' jobber.']:
            calls=[]
            def rejected(prompt):
                calls.append(prompt);return {'status':'SUCCESS','response':json.dumps(dict(candidate,text_vi=changed),ensure_ascii=False)}
            with self.subTest(changed=changed),self.assertRaisesRegex(QualityHold,'legal label'):
                create(self.root,item['id'],sha,CONFIG,rejected,allow_official_tax=True)
            self.assertEqual(len(calls),1)
        # An absent legal label cannot be added to a different source either.
        with self.assertRaisesRegex(QualityHold,'legal label'):
            self.generate([candidate,REVIEW])

    def test_external_draft_with_rewritten_envelope_cannot_bypass_legal_labels(self):
        from source_mirror.official_payload import build
        source=HTML.replace(b'Workplace safety guidance.',b'A jobber and a statutory employee are discussed under the stated conditions.')
        item=next(item for item in SOURCES if item['id']=='irs-worker-classification')
        collect(self.root,item,lambda _:source);sha=hashlib.sha256(source).hexdigest()
        candidate=dict(CANDIDATE,text_vi='Nguồn đề cập jobber và statutory employee theo các điều kiện đã nêu.')
        outputs=iter([candidate,REVIEW])
        result=create(self.root,item['id'],sha,CONFIG,lambda _: {'status':'SUCCESS','response':json.dumps(next(outputs),ensure_ascii=False)},allow_official_tax=True)
        original=Path(result['path']).read_bytes()
        media=[{'id':'figure','sha256':'0'*64,'provenance_url':'https://www.irs.gov/figure.png','credit':'IRS',
                'rights_basis':'Source evidence pending','rights_evidence_sha256':'1'*64,'alt':'Hình minh họa'}]
        editorial={'locales':{'vi':{'summary':'Phân loại người lao động','meta_title':'Phân loại','meta_description':'Nguồn chính thức'},
                             'en':{'summary':'Worker classification','meta_title':'Classification','meta_description':'Official source'}},
                   'hashtags':['business','worker','irs'],'category':'Business','status':'Published','is_public':True,
                   'is_featured':False,'video_urls':[],'published_at':'2030-01-01T08:00:00+07:00'}
        self.assertIsInstance(build(original,media,editorial,allow_official_tax=True),bytes)
        for changed in [candidate['text_vi'].replace('jobber và ',''),
                        candidate['text_vi'].replace('statutory employee','statutory contractor'),
                        candidate['text_vi']+' jobber.']:
            artifact=json.loads(original)
            artifact['candidate']['text_vi']=changed
            # Match the forged generation envelope and keep an apparent MQM
            # PASS: the deterministic source binding must still reject it.
            artifact['generation_envelope']['response']=json.dumps(artifact['candidate'],ensure_ascii=False)
            with self.subTest(changed=changed),self.assertRaisesRegex(ValueError,'legal label'):
                build(json.dumps(artifact,ensure_ascii=False).encode(),media,editorial,allow_official_tax=True)

    def test_rank_preservation_prompt_and_uncertainty_remain_fail_closed(self):
        data=HTML.replace(b'Workplace safety guidance.',b'CAPT Estill provided guidance.')
        collect(self.root,SOURCES[0],lambda _:data); sha=hashlib.sha256(data).hexdigest()
        prompts=[]
        def infer(candidate):
            def call(prompt):
                prompts.append(prompt)
                value=candidate if len(prompts)==1 else REVIEW
                return {'status':'SUCCESS','response':json.dumps(value,ensure_ascii=False)}
            return call
        wrong=dict(CANDIDATE,text_vi='Đại tá Estill đưa ra hướng dẫn.')
        with self.assertRaisesRegex(QualityHold,'rank abbreviation'):
            create(self.root,SOURCES[0]['id'],sha,CONFIG,infer(wrong))
        self.assertIn('Keep CAPT',prompts[0]); self.assertIn('report the uncertainty',prompts[0])
        prompts.clear()
        uncertain=dict(CANDIDATE,text_vi='CAPT Estill đưa ra hướng dẫn.',uncertainties=['dispenser unclear'])
        with self.assertRaises(QualityHold): create(self.root,SOURCES[0]['id'],sha,CONFIG,infer(uncertain))
        self.assertEqual(len(prompts),1)

    def test_n95_precise_translation_required_and_uncertainty_stays_held(self):
        from source_mirror.official_draft import validate_vietnamese_surface
        with self.assertRaisesRegex(ValueError,'N95 filtering facepiece'):
            validate_vietnamese_surface({'reference_text':'N95® filtering facepiece respirator4'},
                {'title_vi':'An toàn','text_vi':'Đeo khẩu trang N954.'},[])
        data=HTML.replace(b'Workplace safety guidance.',b'Wear an N95\xc2\xae filtering facepiece respirator.')
        collect(self.root,SOURCES[0],lambda _:data);sha=hashlib.sha256(data).hexdigest()
        prompts=[]
        def infer(candidate):
            def call(prompt):
                prompts.append(prompt);value=candidate if len(prompts)==1 else REVIEW
                return {'status':'SUCCESS','response':json.dumps(value,ensure_ascii=False)}
            return call
        wrong=dict(CANDIDATE,title_vi='An toàn nơi làm việc',text_vi='Đeo khẩu trang N95.')
        with self.assertRaisesRegex(QualityHold,'N95 filtering facepiece'):create(self.root,SOURCES[0]['id'],sha,CONFIG,infer(wrong))
        self.assertIn('mặt nạ phòng hộ lọc hạt N95',prompts[0]);self.assertIn('visible source link text is itself a URL',prompts[0])
        prompts.clear();good=dict(wrong,text_vi='Đeo mặt nạ phòng hộ lọc hạt N95.')
        self.assertEqual(create(self.root,SOURCES[0]['id'],sha,CONFIG,infer(good))['status'],'HOLD')
        prompts.clear();uncertain=dict(good,uncertainties=['meaning remains unclear'])
        with self.assertRaises(QualityHold):create(self.root,SOURCES[0]['id'],sha,CONFIG,infer(uncertain))
        self.assertEqual(len(prompts),1)

    def test_bounded_fresh_retry_only_for_invalid_json(self):
        prompts=[]; outputs=iter([{'status':'SUCCESS','response':'not JSON'},
                                  {'status':'SUCCESS','response':json.dumps(CANDIDATE,ensure_ascii=False)},
                                  {'status':'SUCCESS','response':json.dumps(REVIEW)}])
        def infer(prompt): prompts.append(prompt); return next(outputs)
        result=create(self.root,SOURCES[0]['id'],self.sha,CONFIG,infer,max_generation_attempts=2)
        self.assertEqual(result['status'],'HOLD'); self.assertEqual(len(prompts),3)
        self.assertIn('Generate a fresh response',prompts[1])
        holds=list((self.root/'official-holds'/SOURCES[0]['id']/self.sha).glob('*.json'))
        self.assertEqual(len(holds),1)
        self.assertEqual(json.loads(holds[0].read_text(encoding='utf-8'))['response'],'not JSON')
        calls=[]
        def auth_fail(_): calls.append(1); return {'status':'ERROR'}
        with self.assertRaises(QualityHold): create(self.root,SOURCES[0]['id'],self.sha,CONFIG,auth_fail,max_generation_attempts=2)
        self.assertEqual(len(calls),1)
        with self.assertRaises(QualityHold): create(self.root,SOURCES[0]['id'],self.sha,CONFIG,auth_fail,max_generation_attempts=3)


if __name__ == '__main__': unittest.main()
