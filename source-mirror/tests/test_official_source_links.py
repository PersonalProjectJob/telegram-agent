import hashlib
import json
import tempfile
from pathlib import Path
import unittest
from source_mirror.official_source_links import source_link_contract, marked_source, validate_translation, render_marked
from source_mirror.official_sources import SOURCES, collect
from source_mirror.official_draft import create
from source_mirror.official_payload import build
from source_mirror.localization import QualityHold, MODEL, EFFORT

HTML=(b'<main><h1>Title</h1><p>Scrub when you <a href="/clean-hands/about/index.html">wash your hands</a>.</p>'
      b'<p>In <a href="https://www.epa.gov/safepestcontrol/preventing-pedicure-foot-spa-infections">'
      b'commercial settings such as nail salons</a> sterilize tools.</p></main>')

class SourceLinkTests(unittest.TestCase):
    def test_order_offsets_and_exact_destinations(self):
        links=source_link_contract(HTML)
        self.assertEqual([(v['paragraph'],v['start'],v['end']) for v in links],[(1,15,30),(2,3,42)])
        self.assertEqual(links[0]['url'],'https://www.cdc.gov/clean-hands/about/index.html')

    def test_removed_changed_or_added_anchor_fails(self):
        changes=(HTML.replace(b'/clean-hands/about/index.html',b'//evil.example/'),
                 HTML.replace(b'wash your hands',b'clean hands'),
                 HTML.replace(b'<a href="/clean-hands/about/index.html">',b''),
                 HTML.replace(b'</main>',b'<p><a href="https://example.com">extra</a></p></main>'),
                 HTML.replace(b'Scrub when you ',b'Scrub when you wash your hands and '))
        for data in changes:
            with self.subTest(data=data), self.assertRaises(ValueError): source_link_contract(data)

    def test_markers_are_paragraph_bound_and_safely_rendered(self):
        translated='Tiêu đề\n\nChà khi ⟦A⟧rửa tay⟦/A⟧.\n\nTại ⟦B⟧tiệm làm móng⟦/B⟧ tiệt trùng dụng cụ.'
        self.assertIn('⟦A⟧wash your hands⟦/A⟧',marked_source(HTML))
        value=render_marked(HTML,translated.replace('Tiêu đề','Tiêu <script>'))
        self.assertIn('&lt;script&gt;',value); self.assertNotIn('<script>',value)
        self.assertIn('href="https://www.cdc.gov/clean-hands/about/index.html"',value)
        for wrong in (translated.replace('⟦A⟧',''),translated.replace('⟦B⟧','⟦A⟧'),
                      translated.replace('⟦A⟧rửa tay⟦/A⟧','rửa tay').replace('Tiêu đề','Tiêu đề ⟦A⟧rửa tay⟦/A⟧'),
                      translated.replace('\n\n','\n',1)):
            with self.subTest(wrong=wrong), self.assertRaises(ValueError): validate_translation(HTML,wrong)
        for wrong_term in ('vệ sinh','khử trùng'):
            with self.subTest(wrong_term=wrong_term), self.assertRaisesRegex(ValueError,'terminology'):
                validate_translation(HTML,translated.replace('tiệt trùng',wrong_term))
        for english,accepted,rejected in ((b'disinfect','khử trùng','tiệt trùng'),(b'sanitize','làm vệ sinh','khử trùng')):
            source=HTML.replace(b'sterilize',english)
            validate_translation(source,translated.replace('tiệt trùng',accepted))
            with self.subTest(term=english), self.assertRaisesRegex(ValueError,'terminology'):
                validate_translation(source,translated.replace('tiệt trùng',rejected))

    def test_draft_and_payload_preserve_both_anchor_destinations(self):
        data=(b'<html><head><title>Healthy Habits: Nail Hygiene | Water, Sanitation, and Environmentally Related Hygiene (WASH) | CDC</title>'
              b'<meta property="cdc:last_reviewed" content="April 16, 2024"></head><body>'
              b'<time class="cdc-page-title-bar__item--date" datetime="2024-04-16 04:00:00">April 16, 2024</time>'
              b'<a href="/ncezid/index.html">National Center for Emerging and Zoonotic Infectious Diseases (NCEZID)</a>'+HTML+b'</body></html>')
        item=next(x for x in SOURCES if x['id']=='cdc-ncezid-nail-hygiene-2024')
        candidate={'title_vi':'Vệ sinh móng năm 2024','text_vi':'Tiêu đề\n\nChà khi ⟦A⟧rửa tay⟦/A⟧.\n\nTại ⟦B⟧tiệm làm móng⟦/B⟧ tiệt trùng dụng cụ.','uncertainties':[]}
        review={'verdict':'PASS','critical':0,'major':0,'minor':0,'issues':[],'uncertainties':[],'glossary_ok':True,'accuracy_ok':True,'technical_clarity_ok':True}
        cfg={'localization':{'enabled':True,'model':MODEL,'effort':EFFORT,'socket_path':'/unused'}}
        with tempfile.TemporaryDirectory() as root:
            collect(root,item,lambda _:data); sha=hashlib.sha256(data).hexdigest(); prompts=[]
            def infer(prompt):
                prompts.append(prompt); return {'status':'SUCCESS','response':json.dumps(candidate if len(prompts)==1 else review,ensure_ascii=False)}
            draft=Path(create(root,item['id'],sha,cfg,infer)['path']).read_bytes()
            self.assertIn('⟦A⟧wash your hands⟦/A⟧',prompts[0]); self.assertIn('⟦B⟧',prompts[1])
            fields={'summary':'Hướng dẫn 2024','meta_title':'Vệ sinh 2024','meta_description':'Hướng dẫn 2024'}
            editorial={'locales':{'vi':fields,'en':fields},'hashtags':['nail','safety','hygiene'],'category':'Safety','status':'Published','is_public':True,'is_featured':False,'video_urls':[],'published_at':'2026-11-01T08:00:00+07:00'}
            media=[{'id':'x','sha256':'a'*64,'provenance_url':'https://www.cdc.gov/x','credit':'CDC','rights_basis':'unverified','rights_evidence_sha256':'b'*64,'alt':'hands'}]
            payload=json.loads(build(draft,media,editorial))
            for locale in ('vi','en'):
                body=payload['locales'][locale]['body_html']
                self.assertIn('https://www.cdc.gov/clean-hands/about/index.html',body)
                self.assertIn('https://www.epa.gov/safepestcontrol/preventing-pedicure-foot-spa-infections',body)
                self.assertNotIn('⟦',body)
            bad=dict(candidate,text_vi=candidate['text_vi'].replace('⟦A⟧',''))
            with self.assertRaisesRegex(QualityHold,'source link translation'):
                create(root,item['id'],sha,cfg,lambda _: {'status':'SUCCESS','response':json.dumps(bad,ensure_ascii=False)})
            for replacement in ('vệ sinh','khử trùng'):
                wrong=dict(candidate,text_vi=candidate['text_vi'].replace('tiệt trùng',replacement))
                with self.subTest(replacement=replacement), self.assertRaisesRegex(QualityHold,'source link translation'):
                    create(root,item['id'],sha,cfg,lambda _: {'status':'SUCCESS','response':json.dumps(wrong,ensure_ascii=False)})
