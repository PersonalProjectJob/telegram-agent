import unittest
from unittest.mock import patch
from source_mirror.official_sources import SOURCES,verify_source_date,fetch
from source_mirror.official_tax import source_is_tax
from source_mirror.official_sba_examples import examples,BUTTONS

class SbaLegacyTests(unittest.TestCase):
    def test_source_is_exact_and_date_footer_is_bound(self):
        item=next(x for x in SOURCES if x['id']=='sba-legacy-business-plan-2025')
        self.assertEqual(item['url'],'https://legacy.sba.gov/business-guide/plan-your-business/write-your-business-plan')
        html=b'<html><head><title>Write your business plan | U.S. Small Business Administration</title></head><main><p>Test</p></main><footer class="basic-page__footer">Last updated November 13, 2025</footer></html>'
        verify_source_date(item,html)
        for bad in (html.replace(b'November 13, 2025',b'November 14, 2025'),html.replace(b'basic-page__footer',b'other'),html.replace(b'Write your business plan',b'Other page'),html+html):
            with self.assertRaises(ValueError):verify_source_date(item,bad)

    def test_other_legacy_sba_urls_are_not_allowed(self):
        item=next(x for x in SOURCES if x['id']=='sba-legacy-business-plan-2025')
        with patch('source_mirror.official_sources.socket.getaddrinfo') as dns:
            with self.assertRaisesRegex(ValueError,'not allowlisted'):fetch(dict(item,url='https://legacy.sba.gov/other'))
            dns.assert_not_called()

    def test_legacy_source_tax_classification_not_bypassed(self):
        item=next(x for x in SOURCES if x['id']=='sba-legacy-business-plan-2025')
        self.assertTrue(source_is_tax(item,'Tax information','IRS income tax'))

    def test_example_buttons_are_bound_to_source_and_tamper_holds(self):
        source=''.join('<a href="'+path+'">'+label+'</a>' for label,path in BUTTONS).encode()
        self.assertEqual(len(examples(source)),3)
        with self.assertRaisesRegex(ValueError,'buttons changed'):examples(source.replace(b'support-andrews-business-plan-template-lean',b'wrong'))
        with self.assertRaisesRegex(ValueError,'buttons changed'):examples(source+source)
