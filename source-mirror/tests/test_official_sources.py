import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError

from source_mirror.official_sources import SOURCES, MAX_BYTES, collect, collect_all, extract, fetch, verify_source_date, require_no_unbound_links
from source_mirror.official_draft import LABELS

FIXTURE=b'<html><head><title>title</title></head><body><nav><p>navigation</p></nav><main><h1>Tax reporting</h1><p>First paragraph.</p><aside><p>advertisement</p></aside><ul><li>Second paragraph.</li></ul><script>ignored()</script></main></body></html>'


class OfficialSourcesTests(unittest.TestCase):
    def test_links_in_extracted_text_are_held_including_changed_or_empty_href(self):
        for href in ('https://www.cdc.gov/example','https://www.cdc.gov/changed',''):
            data=('<html><head><title>x</title></head><body><main><p>Read <a href="'+href+'">here</a>.</p></main></body></html>').encode()
            with self.subTest(href=href), self.assertRaisesRegex(ValueError,'source links'):
                require_no_unbound_links(data)
        require_no_unbound_links(b'<html><head><title>x</title></head><body><nav><a href="/nav">nav</a></nav><main><p>No link.</p></main></body></html>')
    def test_ncezid_identity_date_and_intake_hold(self):
        item=next(x for x in SOURCES if x['id']=='cdc-ncezid-nail-hygiene-2024')
        data=(b'<html><head><title>Healthy Habits: Nail Hygiene | Water, Sanitation, and Environmentally Related Hygiene (WASH) | CDC</title>'
              b'<meta property="cdc:last_reviewed" content="April 16, 2024"></head><body><main>'
              b'<time class="cdc-page-title-bar__item--date" datetime="2024-04-16 04:00:00">April 16, 2024</time>'
              b'<h1>Healthy Habits: Nail Hygiene</h1><p>Clean tools before use.</p>'
              b'<a href="/ncezid/index.html">National Center for Emerging and Zoonotic Infectious Diseases (NCEZID)</a></main></body></html>')
        self.assertEqual(LABELS[item['id']],'CDC/NCEZID')
        verify_source_date(item,data)
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(collect(root,item,lambda _:data)['status'],'expert_review_required')
            self.assertFalse((Path(root)/'articles').exists())
        for altered in (data.replace(b'April 16, 2024',b'April 17, 2024',1),
                        data.replace(b'2024-04-16 04:00:00',b'2024-04-17 04:00:00'),
                        data.replace(b'NCEZID)',b'NIOSH)'),data.replace(b'Healthy Habits: Nail Hygiene |',b'Other |',1)):
            with self.subTest(altered=altered), self.assertRaises(ValueError): verify_source_date(item,altered)
    def test_reference_text_uses_main_and_omits_navigation(self):
        value=extract(FIXTURE)
        self.assertEqual(value,'Tax reporting\n\nFirst paragraph.\n\nSecond paragraph.\n')
        self.assertNotIn('navigation',value); self.assertNotIn('advertisement',value)

    def test_incomplete_ambiguous_or_oversized_markup_is_held(self):
        for data in [b'<p>not main</p>',b'<main><p>incomplete',b'<main><p>a</p></main><main><p>b</p></main>',b'x'*(MAX_BYTES+1)]:
            with self.subTest(size=len(data)), self.assertRaises(ValueError): extract(data)

    def test_immutable_snapshot_stays_held_after_recollection(self):
        with tempfile.TemporaryDirectory() as d:
            first=collect(d,SOURCES[0],lambda _:FIXTURE)
            path=Path(first['snapshot']);meta=json.loads((path/'metadata.json').read_text(encoding='utf-8'))
            self.assertEqual(meta['status'],'expert_review_required');self.assertEqual(meta['source_sha256'],hashlib.sha256(FIXTURE).hexdigest())
            self.assertEqual(meta['source_url'],SOURCES[0]['url']);self.assertFalse((Path(d)/'articles').exists())
            again=collect(d,SOURCES[0],lambda _:FIXTURE)
            self.assertTrue(again['unchanged']);self.assertEqual(first['snapshot'],again['snapshot'])
            meta['status']='approved';(path/'metadata.json').write_text(json.dumps(meta))
            with self.assertRaises(ValueError):collect(d,SOURCES[0],lambda _:FIXTURE)

    def test_mutated_snapshot_and_changed_content_make_distinct_revision(self):
        with tempfile.TemporaryDirectory() as d:
            first=collect(d,SOURCES[0],lambda _:FIXTURE)
            second=collect(d,SOURCES[0],lambda _:FIXTURE.replace(b'First paragraph.',b'Updated paragraph.'))
            self.assertNotEqual(first['snapshot'],second['snapshot']);self.assertEqual(second['status'],'expert_review_required')
            (Path(first['snapshot'])/'reference.txt').write_text('forged',encoding='utf8')
            with self.assertRaises(ValueError):collect(d,SOURCES[0],lambda _:FIXTURE)

    def test_mutating_reference_and_metadata_hash_or_provenance_is_detected(self):
        with tempfile.TemporaryDirectory() as d:
            first=collect(d,SOURCES[0],lambda _:FIXTURE);path=Path(first['snapshot'])
            (path/'reference.txt').write_text('forged',encoding='utf8')
            meta=json.loads((path/'metadata.json').read_text(encoding='utf8'))
            meta['reference_text_sha256']=hashlib.sha256(b'forged').hexdigest()
            (path/'metadata.json').write_text(json.dumps(meta),encoding='utf8')
            with self.assertRaises(ValueError):collect(d,SOURCES[0],lambda _:FIXTURE)

    def test_breadcrumb_and_ad_div_do_not_enter_review_text(self):
        data=b'<html><head><title>Official title</title></head><body><main><p>Home <span class="breadcrumb_last">Current</span></p><h1>Actual heading</h1><div class="advertisement"><img src="x"/><p>Hidden ad</p></div><p>Useful source.</p></main></body></html>'
        self.assertEqual(extract(data),'Actual heading\n\nUseful source.\n')

    def test_only_exact_configured_urls_are_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError): collect(d,dict(SOURCES[0],url='https://www.irs.gov.attacker.example/'),lambda _:FIXTURE)
        with self.assertRaises(ValueError):fetch(dict(SOURCES[0],url='http://127.0.0.1/'))
        removed={'id':'irs-tip-reporting','url':'https://www.irs.gov/businesses/small-businesses-self-employed/tip-recordkeeping-and-reporting','category':'us-tax','scope':'US federal'}
        with patch('source_mirror.official_sources.socket.getaddrinfo') as dns:
            with self.assertRaises(ValueError):fetch(removed)
            dns.assert_not_called()
        self.assertEqual({x['id'] for x in SOURCES if x['category']=='us-tax'}, {'irs-tip-reporting','irs-worker-classification'})

    def test_partial_network_failure_is_reported_without_autopublish(self):
        def fake(root,item):
            if item['id']=='cdc-niosh-nail-technicians':raise HTTPError(item['url'],403,'Forbidden',{},None)
            return {'source':item['id'],'status':'expert_review_required'}
        with patch('source_mirror.official_sources.collect',side_effect=fake):
            result=collect_all('/not-used')
        self.assertEqual(len(result),len(SOURCES));failed=next(x for x in result if x['source']=='cdc-niosh-nail-technicians')
        self.assertEqual(failed['http_status'],403)
        self.assertEqual(failed['status'],'fetch_failed');self.assertTrue(all(x['status']!='ready' for x in result))

    def test_cdc_source_attribution_scope_and_hold(self):
        self.assertEqual(len(SOURCES), 7)
        item = SOURCES[0]
        self.assertEqual(item['url'], 'https://www.cdc.gov/niosh/nail-technicians/about/index.html')
        self.assertEqual(item['category'], 'us-workplace-safety')
        self.assertIn('CDC/NIOSH research', item['scope'])
        self.assertIn('not mandatory OSHA law', item['scope'])
        with tempfile.TemporaryDirectory() as d:
            result = collect(d, item, lambda _: FIXTURE)
            meta = json.loads((Path(result['snapshot']) / 'metadata.json').read_text(encoding='utf-8'))
            self.assertEqual(meta['source_id'], 'cdc-niosh-nail-technicians')
            self.assertEqual(meta['source_url'], item['url'])
            self.assertEqual(meta['source_sha256'], hashlib.sha256(FIXTURE).hexdigest())
            self.assertEqual(meta['status'], 'expert_review_required')
            self.assertFalse((Path(d) / 'articles').exists())
            self.assertEqual({p.name for p in Path(d).iterdir()}, {'official-review-queue'})

    def test_sba_official_source_is_held_and_attributed(self):
        item = next(item for item in SOURCES if item['id'] == 'sba-business-plan')
        self.assertEqual(item['url'], 'https://www.sba.gov/counseling/plan-your-business/')
        with tempfile.TemporaryDirectory() as d:
            result = collect(d, item, lambda _: FIXTURE)
            meta = json.loads((Path(result['snapshot']) / 'metadata.json').read_text(encoding='utf-8'))
            self.assertEqual(meta['source_url'], item['url'])
            self.assertEqual(meta['source_id'], item['id'])
            self.assertEqual(meta['geographic_scope'], item['scope'])
            self.assertEqual(meta['status'], 'expert_review_required')
            self.assertFalse((Path(d) / 'articles').exists())

    def test_url_validation_and_nonpublic_dns_before_request(self):
        for url in ('http://www.cdc.gov/', 'https://user@www.cdc.gov/',
                    'https://www.cdc.gov:444/', 'https://www.cdc.gov/?x=1',
                    'https://www.cdc.gov/#x', 'https://www.cdc.gov.attacker.example/'):
            item = dict(SOURCES[0], url=url)
            with self.subTest(url=url), patch('source_mirror.official_sources.SOURCES', (item,)), patch('source_mirror.official_sources.socket.getaddrinfo') as dns:
                with self.assertRaises(ValueError): fetch(item)
                dns.assert_not_called()
        for address in ('127.0.0.1', '10.0.0.1', '::1', '169.254.169.254'):
            with self.subTest(address=address), patch('source_mirror.official_sources.socket.getaddrinfo', return_value=[(None,None,None,None,(address,443))]), patch('source_mirror.official_sources.build_opener') as opener:
                with self.assertRaises(ValueError): fetch(SOURCES[0])
                opener.assert_not_called()

    def test_fetch_connects_only_checked_ip_with_source_tls_identity(self):
        address=('93.184.216.34',443); raw=MagicMock(); context=MagicMock(); response=MagicMock()
        response.status=200; response.headers.get_content_type.return_value='text/html'
        response.headers.get.return_value='0'; response.read.return_value=b'<html>ok</html>'
        def request(connection,*args,**kwargs):
            self.assertEqual(kwargs['headers']['Connection'], 'close')
            connection.connect()
        with patch('source_mirror.official_sources.socket.getaddrinfo',return_value=[(2,1,0,'',address)]) as dns, \
             patch('source_mirror.official_sources.socket.create_connection',return_value=raw) as connect, \
             patch('source_mirror.official_sources.ssl.create_default_context',return_value=context), \
             patch('source_mirror.official_sources.http.client.HTTPSConnection.request',new=request), \
             patch('source_mirror.official_sources.http.client.HTTPSConnection.getresponse',return_value=response):
            self.assertEqual(fetch(SOURCES[0]),b'<html>ok</html>')
        self.assertEqual(dns.call_count,1); connect.assert_called_once_with(address,timeout=12)
        context.wrap_socket.assert_called_once_with(raw,server_hostname='www.cdc.gov')

    def test_fetch_rejects_redirect_and_non_html(self):
        response=MagicMock(); response.status=302; response.headers.get_content_type.return_value='text/html'
        with patch('source_mirror.official_sources.socket.getaddrinfo',return_value=[(2,1,0,'',('93.184.216.34',443))]), \
             patch('source_mirror.official_sources.http.client.HTTPSConnection.request'), \
             patch('source_mirror.official_sources.http.client.HTTPSConnection.getresponse',return_value=response):
            with self.assertRaises(HTTPError): fetch(SOURCES[0])
            response.status=200; response.headers.get_content_type.return_value='application/json'
            with self.assertRaises(ValueError): fetch(SOURCES[0])

    def test_existing_irs_and_sba_snapshots_are_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            old_paths = [Path(d)/'official-review-queue'/source/'old-revision' for source in ('irs-tip-reporting', 'sba-business-plan', 'osha-nail-ventilation')]
            for old in old_paths:
                old.mkdir(parents=True); (old/'source.html').write_text('historic evidence',encoding='utf-8')
            with patch('source_mirror.official_sources.collect',return_value={'status':'expert_review_required'}):
                collect_all(d)
            for old in old_paths:
                self.assertEqual((old/'source.html').read_text(encoding='utf-8'),'historic evidence')

    def test_irs_intake_collision_with_old_metadata_fails_without_modification(self):
        item=next(x for x in SOURCES if x['id']=='irs-tip-reporting')
        with tempfile.TemporaryDirectory() as d:
            folder=Path(collect(d,item,lambda _:FIXTURE)['snapshot'])
            path=folder/'metadata.json'; meta=json.loads(path.read_text(encoding='utf-8'))
            meta['geographic_scope']='previous policy scope'
            original=json.dumps(meta).encode(); path.write_bytes(original)
            with self.assertRaisesRegex(ValueError,'altered'): collect(d,item,lambda _:FIXTURE)
            self.assertEqual(path.read_bytes(),original)
            self.assertEqual((folder/'source.html').read_bytes(),FIXTURE)


if __name__=='__main__':unittest.main()
