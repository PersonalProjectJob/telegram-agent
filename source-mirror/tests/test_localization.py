import copy
import json
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch, MagicMock

from source_mirror.localization import (QualityHold, strict_json, validate_candidate,
    validate_review, generate_revision, generation_prompt, review_prompt, load_approved, MODEL, EFFORT)
from source_mirror.snapshot import save
from source_mirror.store import Store
from source_mirror.__main__ import once
from source_mirror.agy_broker import infer
from source_mirror.publisher import verify_outgoing_locales, VerificationIncomplete, prepare, canonical_hashtags, content_fingerprint, admin_content, guest_content


class LocalizationTests(unittest.TestCase):
    def test_admin_content_selector_requires_unique_visible_view_body(self):
        details=MagicMock(); content=details.locator.return_value; content.count.return_value=1
        self.assertIs(admin_content(details),content)
        details.locator.assert_called_once_with('.blog-article-content.ql-editor')
        content.wait_for.assert_called_once_with(state='visible',timeout=15000)
        content.count.return_value=2
        with self.assertRaisesRegex(VerificationIncomplete,'not unique'): admin_content(details)
        content.count.return_value=0
        with self.assertRaisesRegex(VerificationIncomplete,'not unique'): admin_content(details)

    def test_guest_content_selector_requires_unique_visible_article_body(self):
        page=MagicMock(); content=page.locator.return_value; content.count.return_value=1
        self.assertIs(guest_content(page),content)
        page.locator.assert_called_once_with('.blog-article-content.ql-editor')
        content.wait_for.assert_called_once_with(state='visible',timeout=30000)
        content.count.return_value=2
        with self.assertRaisesRegex(VerificationIncomplete,'not uniquely identified'): guest_content(page)
        content.count.return_value=0
        with self.assertRaisesRegex(VerificationIncomplete,'not uniquely identified'): guest_content(page)
    def test_native_image_exact_class_is_safe_and_src_remains_bound(self):
        expected='<p>Example</p><p><img class="blog-standalone-image" src="https://cdn.example/image.png"></p>'
        allowed='<p>Example</p><p><img class="blog-standalone-image" src="https://cdn.example/image.png"></p>'
        self.assertEqual(content_fingerprint(expected),content_fingerprint(allowed))
        self.assertEqual(content_fingerprint(expected),content_fingerprint(expected.replace(' class="blog-standalone-image"','')))
        self.assertNotEqual(content_fingerprint(allowed),content_fingerprint(allowed.replace('/image.png','/other.png')))
        first='<p><img src="https://cdn.example/one.png"><img src="https://cdn.example/two.png"></p>'
        swapped='<p><img src="https://cdn.example/two.png"><img src="https://cdn.example/one.png"></p>'
        self.assertNotEqual(content_fingerprint(first),content_fingerprint(swapped))
        fields={'title':'Tiêu đề','summary':'Tóm tắt','meta_title':'Tiêu đề','meta_description':'Tóm tắt'}
        localized={'locales':{'Vietnamese':fields,'English':fields},'hashtags':['gel','nail','art']}
        contents={'Vietnamese':expected,'English':expected}
        sent={'translations':[{'languageCode':code,'title':fields['title'],'summary':fields['summary'],
                               'metaTitle':fields['meta_title'],'metaDescription':fields['meta_description'],'content':allowed}
                              for code in ('VI','EN')],'hashtags':['gel','nail','art']}
        verify_outgoing_locales(sent,localized,contents)
        sent['translations'][0]['content']=allowed.replace(' class="blog-standalone-image"','')
        verify_outgoing_locales(sent,localized,contents)
        sent['translations'][0]['content']=allowed.replace('/image.png','/other.png')
        with self.assertRaisesRegex(VerificationIncomplete,'body differs'): verify_outgoing_locales(sent,localized,contents)
        for image in ('<img class="other" src="https://cdn.example/image.png">',
                      '<img class="blog-standalone-image other" src="https://cdn.example/image.png">',
                      '<img class="blog-standalone-image" src="https://cdn.example/image.png" onerror="alert(1)">',
                      '<img class="blog-standalone-image" src="https://cdn.example/image.png" style="display:none">',
                      '<img class="blog-standalone-image" class="other" src="https://cdn.example/image.png">'):
            with self.subTest(image=image), self.assertRaises(VerificationIncomplete): content_fingerprint(image)
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root)
        self.meta = {'source_url': 'https://example.com/article', 'title': 'Simple gel tutorial'}
        self.blocks = [{'type': 'image', 'media': 0}, {'type': 'html', 'html': '<h3>Step 1</h3>'},
                       {'type': 'html', 'html': '<p>Apply 2 coats of gel polish. Do not cure. <a href="https://example.com/artist">Alice Nali</a></p>'}]
        self.source = {'metadata': self.meta, 'blocks': self.blocks}
        self.candidate = {'title_vi': 'Hướng dẫn sơn gel đơn giản', 'summary_vi': 'Sơn gel theo hướng dẫn.',
                          'meta_title_vi': 'Hướng dẫn sơn gel', 'meta_description_vi': 'Các bước sơn gel dễ theo dõi.',
                          'hashtags': ['gel', 'nail', 'huongdan'], 'uncertainties': [],
                          'blocks': [{'type': 'image', 'media': 0}, {'type': 'html', 'html': '<h3>Bước 1</h3>'},
                                     {'type': 'html', 'html': '<p>Phủ 2 lớp sơn gel. Chưa hơ đèn. <a href="https://example.com/artist">Alice Nali</a></p>'}]}
        self.review = {'verdict': 'PASS', 'critical': 0, 'major': 0, 'minor': 0, 'issues': [], 'uncertainties': [],
                       'glossary_ok': True, 'accuracy_ok': True, 'technical_clarity_ok': True}
        self.config = {'enable_verified_canary': True, 'localization': {'enabled': True, 'model': MODEL, 'effort': EFFORT, 'socket_path': '/not-used'}}

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def article(self):
        aid = save(self.root, self.meta, self.blocks, '<p>original</p>', [])
        self.store.add_ready(self.meta['source_url'], self.meta['title'])
        return {'id': aid}

    def inference(self, value):
        return {'status': 'SUCCESS', 'response': json.dumps(value, ensure_ascii=False), 'usage': {'total_tokens': 1}}

    def test_valid_candidate_and_review(self):
        validate_candidate(self.source, self.candidate)
        validate_review(self.review)

    def test_generic_top_coat_is_in_prompt_and_still_requires_phu(self):
        source=copy.deepcopy(self.source); candidate=copy.deepcopy(self.candidate)
        source['blocks'][2]['html']='<p>Apply top coat.</p>'
        candidate['blocks'][2]['html']='<p>Quét lớp phủ.</p>'
        self.assertIn('"en":"top coat","note":"Preserve the source qualifier; do not invent finish or product type.","vi":"lớp phủ"',generation_prompt(source))
        validate_candidate(source,candidate)
        candidate['blocks'][2]['html']='<p>Quét top coat.</p>'
        with self.assertRaisesRegex(QualityHold,'glossary term missing'): validate_candidate(source,candidate)

    def test_nail_art_missing_technical_details_are_not_invented_or_required(self):
        source=copy.deepcopy(self.source); candidate=copy.deepcopy(self.candidate)
        source['blocks'][2]['html']='<p>Apply gel polish. Cure.</p>'
        candidate['blocks'][2]['html']='<p>Quét sơn gel. Hơ đèn.</p>'
        validate_candidate(source,candidate)
        gen=generation_prompt(source); mqm=review_prompt(source,candidate)
        self.assertIn('omitting lamp type, duration or equipment does not by itself require an uncertainty',gen)
        self.assertIn('Missing nail-art lamp type, duration or equipment alone is not an error',mqm)
        self.assertIn('source statements conflict, numbering is inconsistent',gen)
        self.assertIn('source contradictions, inconsistent step numbering',mqm)
        self.assertIn('invented details',mqm)
        candidate['uncertainties']=['conflicting steps']
        with self.assertRaises(QualityHold): validate_candidate(source,candidate)
        with self.assertRaises(QualityHold): validate_review(dict(self.review,uncertainties=['conflicting steps']))

    def test_changed_media_links_numbers_negation_or_markup_held(self):
        candidates = []
        for replacement in ['<p>Phủ 3 lớp sơn gel. Chưa hơ đèn. <a href="https://example.com/artist">Alice Nali</a></p>',
                            '<p>Phủ 2 lớp sơn gel. Hơ đèn. <a href="https://example.com/artist">Alice Nali</a></p>',
                            '<p>Phủ 2 lớp sơn gel. Chưa hơ đèn. <a href="https://evil.example/artist">Alice Nali</a></p>',
                            '<p onclick="alert(1)">Phủ 2 lớp sơn gel. Chưa hơ đèn.</p>']:
            value = copy.deepcopy(self.candidate); value['blocks'][2]['html'] = replacement; candidates.append(value)
        value = copy.deepcopy(self.candidate); value['blocks'][0]['media'] = 1; candidates.append(value)
        value = copy.deepcopy(self.candidate); value['blocks'].pop(); candidates.append(value)
        for value in candidates:
            with self.subTest(value=value), self.assertRaises(QualityHold):
                validate_candidate(self.source, value)

    def test_generic_collaboration_anchor_translates_but_names_and_urls_stay_protected(self):
        source=copy.deepcopy(self.source); candidate=copy.deepcopy(self.candidate)
        source['blocks'][2]['html']='<p><a href="https://example.com/artist">Andreea Simona Ignat</a> and <a href="https://example.com/brand">Lecenté</a>. <a href="https://bit.ly/3IUJtJN">Read about the collaboration here</a>.</p>'
        candidate['blocks'][2]['html']='<p><a href="https://example.com/artist">Andreea Simona Ignat</a> và <a href="https://example.com/brand">Lecenté</a>. <a href="https://bit.ly/3IUJtJN">Đọc về màn hợp tác tại đây</a>.</p>'
        validate_candidate(source,candidate)
        for before,after in (('https://bit.ly/3IUJtJN','https://evil.example/read'),
                             ('Andreea Simona Ignat','Andreea Khác'),('Lecenté','Lecenté'),
                             ('Đọc về màn hợp tác tại đây','Hợp tác với Alice Nali')):
            changed=copy.deepcopy(candidate); changed['blocks'][2]['html']=changed['blocks'][2]['html'].replace(before,after)
            with self.subTest(before=before), self.assertRaises(QualityHold): validate_candidate(source,changed)

    def test_false_pass_invalid_counts_or_boolean_held(self):
        for field, value in [('minor', 1), ('critical', True), ('glossary_ok', 'true'), ('uncertainties', ['timing unclear'])]:
            review = copy.deepcopy(self.review); review[field] = value
            with self.subTest(field=field), self.assertRaises(QualityHold):
                validate_review(review)
        review = copy.deepcopy(self.review)
        review['issues'] = [{'block': '2', 'category': 'Accuracy', 'severity': 'major', 'reason': 'Changed fact'}]
        with self.assertRaises(QualityHold): validate_review(review)

    def test_duplicate_keys_and_malformed_model_json_held(self):
        for raw in ['{"verdict":"HOLD","verdict":"PASS"}', '```json\n{}\n```', '{', '{"a":NaN}']:
            with self.subTest(raw=raw), self.assertRaises(QualityHold): strict_json(raw)

    def test_revision_binds_independent_review_and_preserves_source(self):
        article = self.article(); manifest = (self.root/'articles'/article['id']/'snapshot-manifest.json').read_bytes()
        calls = []
        def inference(prompt):
            calls.append(prompt)
            return self.inference(self.candidate if len(calls) == 1 else self.review)
        article['localization_revision'] = generate_revision(self.config, self.store, article, inference)
        payload = load_approved(self.store, article)
        self.assertEqual(payload['locales']['English']['blocks'], self.blocks)
        self.assertEqual(payload['locales']['Vietnamese']['blocks'], self.candidate['blocks'])
        self.assertEqual(len(calls), 2)
        self.assertIn('fresh independent', calls[1])
        self.assertEqual(manifest, (self.root/'articles'/article['id']/'snapshot-manifest.json').read_bytes())
        path = Path(article['localization_revision'])/'payload.json'
        path.write_text('{}')
        with self.assertRaises(QualityHold): load_approved(self.store, article)

    def test_generator_uncertainty_prevents_reviewer_call(self):
        article = self.article(); candidate = copy.deepcopy(self.candidate); candidate['uncertainties'] = ['Missing technical instruction']
        with self.assertRaises(QualityHold): generate_revision(self.config, self.store, article, lambda _: self.inference(candidate))
        self.assertEqual(self.store.status()['attempts'], [])

    def test_one_fresh_json_retry_is_recorded_and_still_passes_all_gates(self):
        for first in ('','  '):
            article=self.article(); calls=[]
            responses=iter([{'status':'SUCCESS','response':first},self.inference(self.candidate),self.inference(self.review)])
            def infer(prompt): calls.append(prompt); return next(responses)
            config=copy.deepcopy(self.config); config['localization']['retry_empty_response']=True
            revision=Path(generate_revision(config,self.store,article,infer))
            gate=json.loads((revision/'gate.json').read_text(encoding='utf-8'))
            self.assertEqual(len(calls),3); self.assertIn('fresh complete response',calls[1])
            self.assertEqual(gate['json_retry_stage'],'generation')
            self.assertEqual(json.loads((revision/'generation-first-attempt.json').read_text(encoding='utf-8'))['response'],first)
            self.assertEqual(gate['decision'],'PASS')

    def test_json_retry_limit_and_no_retry_on_uncertainty_or_auth_failure(self):
        article=self.article()
        config=copy.deepcopy(self.config); config['localization']['retry_empty_response']=True
        for responses in ([{'status':'SUCCESS','response':''},{'status':'SUCCESS','response':''}],
                          [{'status':'SUCCESS','response':'```json\n{"uncertainties":["unclear"]}\n```'}],
                          [{'status':'SUCCESS','response':'```json\n{"verdict":"HOLD"}\n```'}],
                          [{'status':'SUCCESS','response':'{'}],
                          [self.inference(dict(self.candidate,uncertainties=['unclear']))],
                          [{'status':'ERROR','response':''}],
                          [{'status':'SUCCESS','response':'{"x":1,"x":2}'}]):
            calls=[]; values=iter(responses)
            def infer(prompt): calls.append(prompt); return next(values)
            with self.subTest(first=responses[0]), self.assertRaises(QualityHold): generate_revision(config,self.store,article,infer)
            self.assertEqual(len(calls),len(responses))
        self.assertEqual(self.store.status()['attempts'],[])
        calls=[]
        with self.assertRaises(QualityHold):
            generate_revision(self.config,self.store,article,lambda prompt:(calls.append(prompt) or {'status':'SUCCESS','response':''}))
        self.assertEqual(len(calls),1)

    def test_review_json_retry_cannot_turn_uncertainty_into_pass(self):
        article=self.article(); calls=[]
        config=copy.deepcopy(self.config); config['localization']['retry_empty_response']=True
        responses=iter([self.inference(self.candidate),{'status':'SUCCESS','response':''},self.inference(dict(self.review,uncertainties=['unclear']))])
        def infer(prompt): calls.append(prompt); return next(responses)
        with self.assertRaisesRegex(QualityHold,'MQM errors'): generate_revision(config,self.store,article,infer)
        self.assertEqual(len(calls),3)
        calls=[]; responses=iter([{'status':'SUCCESS','response':''},self.inference(self.candidate),{'status':'SUCCESS','response':''}])
        def infer_again(prompt): calls.append(prompt); return next(responses)
        with self.assertRaisesRegex(QualityHold,'invalid inference success envelope'): generate_revision(config,self.store,article,infer_again)
        self.assertEqual(len(calls),3)

    def test_missing_curing_detail_is_translated_without_inventing_method(self):
        self.blocks[2]['html'] = '<p>Apply two layers, curing after both layers.</p>'
        article = self.article()
        candidate=copy.deepcopy(self.candidate)
        candidate['blocks'][2]['html']='<p>Phủ hai lớp, hơ đèn sau cả hai lớp.</p>'
        calls=[]
        def infer(prompt):
            calls.append(prompt); return self.inference(candidate if len(calls)==1 else self.review)
        result=generate_revision(self.config,self.store,article,infer)
        self.assertEqual(len(calls),2); self.assertTrue(Path(result,'payload.json').is_file())

    def test_inconsistent_step_numbering_remains_held_before_inference(self):
        self.blocks=[{'type':'html','html':'<h3>Step 1</h3>'}, {'type':'html','html':'<h3>Step 2</h3>'},
                     {'type':'html','html':'<h3>Step 2</h3>'}, {'type':'html','html':'<h3>Step 4</h3>'}]
        article=self.article()
        with self.assertRaisesRegex(QualityHold,'source step numbering inconsistent'):
            generate_revision(self.config,self.store,article,lambda _:self.fail('must not call model'))

    def test_auth_failure_or_invalid_success_held(self):
        article = self.article()
        with self.assertRaises(QualityHold):
            generate_revision(self.config, self.store, article, lambda _: {'status': 'ERROR', 'response': json.dumps(self.candidate)})
        with self.assertRaises(QualityHold):
            generate_revision(self.config, self.store, article, lambda _: (_ for _ in ()).throw(TimeoutError()))

    def test_quality_hold_survives_crawler_error_and_recapture(self):
        article = self.article()
        self.store.quality_hold(article['id'], 'technical ambiguity')
        self.store.hold(self.meta['source_url'], 'extraction error')
        self.store.add_ready(self.meta['source_url'], self.meta['title'])
        row = self.store.status()['articles'][0]
        self.assertEqual(row['state'], 'held')
        self.assertTrue(row['reason'].startswith('localization:'))

    def test_quality_failure_creates_no_publish_attempt_or_slot(self):
        self.article(); self.store.set_paused(False)
        with patch('source_mirror.localization.generate_revision', side_effect=QualityHold('ambiguity')):
            with self.assertRaises(QualityHold): once(self.config, self.store, '2026-10-29', '12:00')
        self.assertEqual(self.store.status()['attempts'], [])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM slots').fetchone()[0], 0)

    def test_previously_approved_tax_revision_is_held_before_reservation(self):
        for field, text in [('title_vi','Hướng dẫn thuế'), ('meta_description_vi','Cách khai thuế'), ('hashtags',['TaxTips','gel','nail'])]:
            with self.subTest(field=field):
                article = self.article()
                candidate = copy.deepcopy(self.candidate); candidate[field] = text
                calls = []
                def inference(prompt):
                    calls.append(prompt)
                    return self.inference(candidate if len(calls) == 1 else self.review)
                revision = generate_revision(self.config, self.store, article, inference)
                article['localization_revision'] = revision
                with self.assertRaisesRegex(QualityHold, 'tax topics held'):
                    load_approved(self.store, article)
                self.store.set_paused(False)
                with patch('source_mirror.localization.generate_revision',return_value=revision), patch('source_mirror.publisher.publish') as publish:
                    with self.assertRaises(QualityHold): once(self.config, self.store, '2026-10-29', '12:00')
                    publish.assert_not_called()
                self.assertEqual(self.store.db.execute('SELECT count(*) FROM attempts').fetchone()[0], 0)
                self.assertEqual(self.store.db.execute('SELECT count(*) FROM slots').fetchone()[0], 0)
                row=self.store.db.execute('SELECT state,reason FROM articles WHERE id=?',(article['id'],)).fetchone()
                self.assertEqual(row['state'],'held')
                self.assertIn('tax topics held',row['reason'])
                self.store.db.execute("UPDATE articles SET state='ready', reason=NULL WHERE id=?",(article['id'],))

    def test_disabled_config_never_publishes_original(self):
        self.article(); self.store.set_paused(False)
        with self.assertRaises(QualityHold): once({'enable_verified_canary': True}, self.store)
        self.assertEqual(self.store.status()['attempts'], [])

    def test_broker_fixed_model_and_no_shell(self):
        class Result:
            returncode = 0
            stdout = '{"status":"SUCCESS","response":"{}"}'
        with patch('source_mirror.agy_broker.subprocess.run', return_value=Result()) as run:
            infer({'agy_binary': sys.executable, 'workdir': '/tmp'}, {'prompt': 'public article'})
        args, kwargs = run.call_args
        self.assertIn(MODEL, args[0]); self.assertIn(EFFORT, args[0]); self.assertNotIn('shell', kwargs)
        with self.assertRaises(ValueError): infer({'agy_binary': sys.executable, 'workdir': '/tmp'}, {'prompt': 'x', 'model': 'other'})

    def test_native_request_guard_rejects_locale_and_tag_mutations(self):
        fields={'title':'Tiêu đề mẫu','summary':'Mô tả','meta_title':'Meta','meta_description':'Nội dung Meta'}
        localized={'locales':{'Vietnamese':fields,'English':dict(fields,title='English title')},'hashtags':['gel','nail','art']}
        contents={'Vietnamese':'<p>Xin <strong>chào</strong></p>','English':'<p>Hello <em>nails</em></p>'}
        sent={'translations':[dict(languageCode=code,title=localized['locales'][lang]['title'],summary='Mô tả',metaTitle='Meta',metaDescription='Nội dung Meta',content=contents[lang]) for code,lang in [('VI','Vietnamese'),('EN','English')]],'hashtags':['gel','nail','art']}
        verify_outgoing_locales(sent,localized,contents)
        prefixed=copy.deepcopy(localized);prefixed['hashtags']=['#gel','#nail','#art']
        verify_outgoing_locales(sent,prefixed,contents)
        equivalent=copy.deepcopy(sent);equivalent['translations'][0]['content']='<p>Xin <strong>ch</strong><strong>ào</strong></p>'
        verify_outgoing_locales(equivalent,localized,contents)
        mutated=[]
        for key in ['title','summary','metaTitle','metaDescription','content','languageCode']:
            value=copy.deepcopy(sent);value['translations'][0][key]='wrong';mutated.append(value)
        value=copy.deepcopy(sent);value['translations'].pop();mutated.append(value)
        value=copy.deepcopy(sent);value['translations'][0]['languageCode']='Vi';mutated.append(value)
        value=copy.deepcopy(sent);value['translations'][1]['languageCode']='VI';mutated.append(value)
        value=copy.deepcopy(sent);value['hashtags']=['gel','nail','other'];mutated.append(value)
        for value in mutated:
            with self.subTest(value=value),self.assertRaises(VerificationIncomplete):verify_outgoing_locales(value,localized,contents)

    def test_canonical_hashtag_collision_or_malformed_prefix_held_before_reservation(self):
        for tags in [['#gel','gel','art'],['#a','nail','art'],['##gel','nail','art'],['#','nail','art']]:
            candidate=copy.deepcopy(self.candidate);candidate['hashtags']=tags
            with self.subTest(tags=tags),self.assertRaises(QualityHold):validate_candidate(self.source,candidate)
        self.assertEqual(canonical_hashtags(['#gel','nail','#art']),['gel','nail','art'])
        with self.assertRaises(VerificationIncomplete):canonical_hashtags(['#gel','nail','art'],allow_prefix=False)
        article=self.article();candidate=copy.deepcopy(self.candidate);candidate['hashtags']=['#gel','gel','art']
        with self.assertRaises(QualityHold):generate_revision(self.config,self.store,article,lambda _:self.inference(candidate))
        self.assertEqual(self.store.status()['attempts'],[])

    def test_cure_glossary_requires_negated_lamp_instruction(self):
        for translated in ['<p>Phủ 2 lớp sơn gel. Làm khô. <a href="https://example.com/artist">Alice Nali</a></p>',
                           '<p>Phủ 2 lớp sơn gel. Không đổi màu; hơ đèn. <a href="https://example.com/artist">Alice Nali</a></p>']:
            candidate=copy.deepcopy(self.candidate);candidate['blocks'][2]['html']=translated
            with self.assertRaises(QualityHold):validate_candidate(self.source,candidate)

    def test_publishing_runtime_blocks_service_workers_before_navigation(self):
        folder=self.root/'mock-browser-input';folder.mkdir()
        for name,value in [('metadata.json',self.meta),('content.json',self.blocks),('media-manifest.json',[{'path':'media/image.png'}])]:
            (folder/name).write_text(json.dumps(value),encoding='utf-8')
        runtime=MagicMock()
        runtime.chromium.launch_persistent_context.side_effect=RuntimeError('stop before browser navigation')
        manager=MagicMock();manager.__enter__.return_value=runtime
        approved={'locales':{'Vietnamese':{'summary':'Bản thử'}}}
        with patch('source_mirror.publisher.verify_snapshot',return_value=folder), patch('source_mirror.localization.load_approved',return_value=approved), patch('playwright.sync_api.sync_playwright',return_value=manager):
            with self.assertRaisesRegex(RuntimeError,'stop before browser navigation'):
                prepare(self.config,self.store,{'id':'test'},1)
        launch=runtime.chromium.launch_persistent_context
        launch.assert_called_once()
        self.assertEqual(launch.call_args.kwargs.get('service_workers'),'block')


if __name__ == '__main__':
    unittest.main()
