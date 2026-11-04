import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from source_mirror import official_link_contract as contract
from source_mirror.official_sources import SOURCES, collect
from source_mirror.official_draft import create
from source_mirror.official_payload import build
from source_mirror.localization import MODEL, EFFORT, QualityHold

BASE='https://www.irs.gov/businesses/small-businesses-self-employed/independent-contractor-self-employed-or-employee'
SOURCE=(b'<html><head><title>Worker classification</title></head><body><main><h1>Worker classification</h1>'
        b'<p>See <a href="/forms-pubs">forms</a> and <a href="/forms-pubs/about-form-ss-8">forms</a> for details.</p>'
        b'<p><a id="SE" name="SE"></a>More information.</p></main></body></html>')

class GenericLinkContractTests(unittest.TestCase):
    def test_adjacent_numeric_citations_keep_distinct_boundaries_and_values(self):
        from source_mirror.official_sources import extract
        raw=(b'<main><p>Female<span><a href="https://www.cdc.gov/a">1</a>'
             b'<a href="https://www.cdc.gov/b">2</a></span>; 12 workers.</p></main>')
        self.assertEqual(extract(raw), 'Female1 2; 12 workers.\n')
        refs=contract.links(raw,BASE)
        self.assertEqual([(x['text'],x['start'],x['end']) for x in refs], [('1',6,7),('2',8,9)])
        marked=contract.marked_source(raw,BASE)
        self.assertEqual(marked,'Female⟦A⟧1⟦/A⟧ ⟦B⟧2⟦/B⟧; 12 workers.\n')
        self.assertIn('12 workers',contract.render(raw,BASE,marked,source_language=True))
        with self.assertRaises(ValueError):
            contract.validate_translation(raw,BASE,'Phụ nữ⟦B⟧2⟦/B⟧ ⟦A⟧1⟦/A⟧; 12 người.')
    def test_visible_url_anchor_must_remain_exact_inside_its_marker(self):
        raw=(b'<main><p>Read <a href="https://www.irs.gov/docs/a">https://www.irs.gov/docs/a</a>.</p></main>')
        exact='Đọc ⟦A⟧https://www.irs.gov/docs/a⟦/A⟧.'
        self.assertEqual(len(contract.validate_translation(raw,BASE,exact)),1)
        for value in ('Đọc ⟦A⟧nguồn⟦/A⟧.','Đọc ⟦A⟧https://www.irs.gov/docs/b⟦/A⟧.'):
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,'visible source URL changed'):
                contract.validate_translation(raw,BASE,value)
    def test_literal_url_anchor_is_not_untranslated_english_prose(self):
        from source_mirror.official_draft import validate_vietnamese_surface
        label='https://www.irs.gov/businesses/small-businesses-self-employed/a'
        source={'reference_text':'See '+label};candidate={'title_vi':'Thông tin','text_vi':'Xem ⟦A⟧'+label+'⟦/A⟧.'}
        validate_vietnamese_surface(source,candidate,[{'text':label}])
        with self.assertRaisesRegex(ValueError,'descriptive source anchor untranslated'):
            validate_vietnamese_surface(source,{'title_vi':'Thông tin','text_vi':'⟦A⟧Read about this program here⟦/A⟧'},
                                        [{'text':'Read about this program here'}])
    def test_repeated_labels_bind_distinct_locations_urls_and_order(self):
        links=contract.links(SOURCE,BASE)
        self.assertEqual([x['url'] for x in links],['https://www.irs.gov/forms-pubs','https://www.irs.gov/forms-pubs/about-form-ss-8'])
        self.assertLess(links[0]['start'],links[1]['start'])
        translated='Phân loại người lao động\n\nXem ⟦A⟧biểu mẫu⟦/A⟧ và ⟦B⟧biểu mẫu⟦/B⟧ để biết chi tiết.\n\nThông tin thêm.'
        value=contract.render(SOURCE,BASE,translated)
        self.assertIn('href="https://www.irs.gov/forms-pubs"',value)
        self.assertIn('href="https://www.irs.gov/forms-pubs/about-form-ss-8"',value)
        self.assertNotIn('⟦',value)
        original=contract.render(SOURCE,BASE,contract.marked_source(SOURCE,BASE),source_language=True)
        self.assertEqual(original.count('<a href='),2)

    def test_missing_moved_swapped_markers_and_unsafe_source_url_hold(self):
        candidate='Phân loại\n\nXem ⟦A⟧mẫu⟦/A⟧ và ⟦B⟧mẫu⟦/B⟧.\n\nThông tin thêm.'
        for bad in (candidate.replace('⟦A⟧',''),candidate.replace('⟦A⟧mẫu⟦/A⟧','mẫu').replace('Phân loại','Phân loại ⟦A⟧mẫu⟦/A⟧'),
                    candidate.replace('⟦A⟧mẫu⟦/A⟧ và ⟦B⟧mẫu⟦/B⟧','⟦B⟧mẫu⟦/B⟧ và ⟦A⟧mẫu⟦/A⟧')):
            with self.subTest(bad=bad), self.assertRaises(ValueError): contract.validate_translation(SOURCE,BASE,bad)
        with self.assertRaises(ValueError): contract.links(SOURCE.replace(b'/forms-pubs',b'javascript:alert(1)',1),BASE)

    def test_href_change_is_bound_to_source_snapshot(self):
        original=contract.links(SOURCE,BASE)
        changed=contract.links(SOURCE.replace(b'/forms-pubs/about-form-ss-8',b'/changed'),BASE)
        self.assertNotEqual(original[1]['url'],changed[1]['url'])

    def test_source_http_and_plain_mailto_are_preserved_without_fetch_or_send(self):
        source=(b'<main><p>See <a href="http://example.gov/path?x=1">reference</a> '
                b'and <a href="mailto:office@example.gov">contact</a>.</p></main>')
        refs=contract.links(source,BASE)
        self.assertEqual([x['url'] for x in refs],['http://example.gov/path?x=1','mailto:office@example.gov'])
        rendered=contract.render(source,BASE,'Xem ⟦A⟧nguồn⟦/A⟧ và ⟦B⟧liên hệ⟦/B⟧.')
        self.assertIn('href="http://example.gov/path?x=1"',rendered)
        self.assertIn('href="mailto:office@example.gov"',rendered)
        for unsafe in ('javascript:alert(1)','http://user:secret@example.gov/',
                       'mailto:office@example.gov?bcc=evil@example.com','mailto:office%0d@example.gov','data:text/html,hi'):
            with self.subTest(unsafe=unsafe), self.assertRaises(ValueError):
                contract.links(source.replace(b'http://example.gov/path?x=1',unsafe.encode()),BASE)

    def test_link_whitespace_empty_and_nested_inline_preserve_positions(self):
        source=(b'<main><p>Read <a href="https://www.irs.gov/a"> Toluene </a> '
                b'<a href="https://www.irs.gov/empty"></a><a href="https://www.irs.gov/b"><strong>More</strong></a>.</p></main>')
        links=contract.links(source,BASE)
        self.assertEqual([x['text'] for x in links],['Toluene','','More'])
        marked=contract.marked_source(source,BASE)
        self.assertIn('⟦A⟧Toluene⟦/A⟧',marked); self.assertIn('⟦B⟧⟦/B⟧',marked)
        rendered=contract.render(source,BASE,'Đọc ⟦A⟧Toluene⟦/A⟧ ⟦B⟧⟦/B⟧⟦C⟧Thêm⟦/C⟧.')
        self.assertEqual(rendered.count('<a href='),3)
        with self.assertRaises(ValueError):
            contract.links(source.replace(b'<a href="https://www.irs.gov/empty"></a>',b'<a href="https://www.irs.gov/empty"><img src="x"></a>'),BASE)

    def test_loopback_private_and_encoded_hosts_hold(self):
        for href in ('https://127.0.0.1/admin','https://[::1]/','https://[::ffff:127.0.0.1]/',
                     'https://192.168.1.1/','https://localhost/','https://x.localhost/',
                     'https://%31%32%37.0.0.1/','https://2130706433/','https://0x7f.0.0.1/',
                     'https://example.com\\@127.0.0.1/'):
            with self.subTest(href=href), self.assertRaises(ValueError):
                contract.links(SOURCE.replace(b'/forms-pubs',href.encode(),1),BASE)

    def test_discarded_breadcrumb_anchors_do_not_shift_article_links(self):
        source=(b'<main><p><a href="https://www.sba.gov/">Home</a> '
                b'<span class="breadcrumb_last">Current</span></p><p>Read '
                b'<a href="https://www.sba.gov/guide">guide</a>.</p></main>')
        refs=contract.links(source,'https://www.sba.gov/counseling/plan-your-business/')
        self.assertEqual(len(refs),1); self.assertEqual(refs[0]['text'],'guide')
        self.assertIn('⟦A⟧guide⟦/A⟧',contract.marked_source(source,'https://www.sba.gov/counseling/plan-your-business/'))

    def test_irs_linked_candidate_renders_and_changed_markers_hold(self):
        source=(b'<html><head><title>Worker classification</title></head><body><main><h1>Worker classification</h1>'
                b'<p>See <a href="https://www.irs.gov/businesses/small-businesses-self-employed/independent-contractor-defined">independent contractor</a>.</p>'
                b'<p>See <a href="https://www.irs.gov/businesses/small-businesses-self-employed/employee-common-law-employee">employee</a>.</p></main></body></html>')
        item=next(x for x in SOURCES if x['id']=='irs-worker-classification')
        candidate={'title_vi':'Phân loại người lao động','text_vi':'Phân loại người lao động\n\nXem ⟦A⟧nhà thầu độc lập⟦/A⟧.\n\nXem ⟦B⟧nhân viên⟦/B⟧.','uncertainties':[]}
        review={'verdict':'PASS','critical':0,'major':0,'minor':0,'issues':[],'uncertainties':[],'glossary_ok':True,'accuracy_ok':True,'technical_clarity_ok':True}
        cfg={'localization':{'enabled':True,'model':MODEL,'effort':EFFORT,'socket_path':'/unused'}}
        with tempfile.TemporaryDirectory() as root:
            collect(root,item,lambda _:source); sha=hashlib.sha256(source).hexdigest(); calls=[]
            def infer(prompt): calls.append(prompt); return {'status':'SUCCESS','response':json.dumps(candidate if len(calls)==1 else review,ensure_ascii=False)}
            result=create(root,item['id'],sha,cfg,infer,allow_official_tax=True)
            draft=Path(result['path']).read_bytes(); self.assertIn('source_links',calls[0]); self.assertEqual(len(calls),2)
            field={'summary':'Thông tin chung','meta_title':'Phân loại','meta_description':'Thông tin chung'}
            editorial={'locales':{'vi':field,'en':field},'hashtags':['nail','work','tax'],'category':'Safety','status':'Published','is_public':True,'is_featured':False,'video_urls':[],'published_at':'2026-11-01T08:00:00+07:00'}
            media=[{'id':'x','sha256':'a'*64,'provenance_url':'https://www.irs.gov/x','credit':'IRS','rights_basis':'unverified','rights_evidence_sha256':'b'*64,'alt':'illustration'}]
            payload=json.loads(build(draft,media,editorial,allow_official_tax=True))
            for locale in ('vi','en'):
                html=payload['locales'][locale]['body_html']
                self.assertIn('independent-contractor-defined',html); self.assertIn('employee-common-law-employee',html)
            bad=dict(candidate,text_vi=candidate['text_vi'].replace('⟦B⟧',''))
            with self.assertRaisesRegex(QualityHold,'source link translation'):
                create(root,item['id'],sha,cfg,lambda _: {'status':'SUCCESS','response':json.dumps(bad,ensure_ascii=False)},allow_official_tax=True)
