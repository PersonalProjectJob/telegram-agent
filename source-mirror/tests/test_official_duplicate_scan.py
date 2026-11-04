"""Real browser fixture; all responses are local route fulfillments."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch
from source_mirror.official_duplicate_scan import check,_known_unrelated_bare_host,DuplicateSource

class DuplicateScanTests(unittest.TestCase):
    def run_scan(self,mutation=None):
        try: from playwright.sync_api import sync_playwright
        except ImportError:self.skipTest('Playwright unavailable')
        with sync_playwright() as p:
            if not Path(p.chromium.executable_path).is_file():self.skipTest('Chromium unavailable')
            browser=p.chromium.launch(headless=True)
            context=browser.new_context(service_workers='block')
            calls=[]
            html='''<button aria-label="Trang sau" onclick="load(++n)">Next</button><script>
            let n=1; function load(i){fetch('https://api.nailhub.ai/api/v1/admin/blogs?pageNumber='+i+'&pageSize=10')};load(n)
            </script>'''
            def route(r):
                from urllib.parse import urlsplit,parse_qs
                u=urlsplit(r.request.url)
                if u.hostname=='nailhub.ai' and u.path=='/admin/blogs':return r.fulfill(status=200,content_type='text/html',body=html)
                if u.hostname!='api.nailhub.ai' or u.path!='/api/v1/admin/blogs':return r.abort()
                n=int(parse_qs(u.query)['pageNumber'][0]);calls.append(n)
                items=[{'id':str(i),'externalSourceUrl':f'https://example.com/{i}'} for i in (range(10) if n==1 else range(10,12))]
                if mutation=='duplicate' and n==2:items[0]['externalSourceUrl']='https://www.cdc.gov/example'
                if mutation=='bare' and n==2:items[0]['externalSourceUrl']='even.vlinkpay.com'
                if mutation=='changed' and len(calls)>2:items[0]['id']='changed'
                if mutation=='missing':items[0].pop('externalSourceUrl')
                value={'items':items,'pageNumber':n,'totalCount':12,'totalPages':2,'hasPreviousPage':n>1,'hasNextPage':n<2}
                if mutation=='count':value['totalCount']=11
                return r.fulfill(status=200,content_type='application/json',headers={'access-control-allow-origin':'*'},body=json.dumps(value))
            context.route('**/*',route);page=context.new_page()
            if mutation and mutation!='bare':
                with self.assertRaises(DuplicateSource if mutation=='duplicate' else ValueError):check(page,'https://www.cdc.gov/example')
            else:
                self.assertEqual(check(page,'https://www.cdc.gov/example')['status'],'SOURCE_ABSENT_NONATOMIC')
                self.assertEqual(calls,[1,2,1,2])
            context.close();browser.close()
    def test_two_complete_scans(self):self.run_scan()
    def test_duplicate(self):self.run_scan('duplicate')
    def test_changed_between_scans(self):self.run_scan('changed')
    def test_missing_source(self):self.run_scan('missing')
    def test_invalid_count(self):self.run_scan('count')
    def test_observed_bare_domain(self):self.run_scan('bare')
    def test_bare_host_ambiguous_values_are_not_ignored(self):
        for value in ['cdc.gov','www.cdc.gov.evil.com','cdc-gov.example','even.vlinkpay.com/cdc.gov',
                      'even.vlinkpay.com?redirect=cdc.gov','user@even.vlinkpay.com',
                      'even%2evlinkpay.com','//even.vlinkpay.com','even.vlinkpay.com:443',
                      ' even.vlinkpay.com','even..vlinkpay.com']:
            with self.subTest(value=value):self.assertFalse(_known_unrelated_bare_host(value))
        self.assertTrue(_known_unrelated_bare_host('even.vlinkpay.com'))
        for value in ['www.cdc.gov.evil.com','www.cdc.gov/example','even.vlinkpay.com?target=cdc.gov','user@even.vlinkpay.com']:
            with self.subTest(value=value),patch('source_mirror.official_duplicate_scan._one',return_value=((1,1),((('id',value),),))),self.assertRaises(ValueError):
                check(None,'https://www.cdc.gov/example')
