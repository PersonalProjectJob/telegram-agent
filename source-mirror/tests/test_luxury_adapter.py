"""Regression tests for the observed non-seasonal Scratch step-by-step layout."""
import unittest
import sqlite3
from types import SimpleNamespace
from playwright.sync_api import sync_playwright, Error
from source_mirror.crawler import EXTRACT, CURATED_LUXURY_URLS, include_curated_luxury, skip_known
from source_mirror.store import identity


def document(taxonomy='Step by Step', extra=''):
    return f'''<div class="fl-col"><h1>Crystal nail tutorial</h1>
    <div class="fl-module fl-module-photo"><img src="https://www.scratchmagazine.co.uk/hero.jpg"></div>
    <div class="fl-module-rich-text"><div class="fl-rich-text"><p>{taxonomy}</p></div></div>
    <div class="fl-module-rich-text"><div class="fl-rich-text"><p>Intro text.</p></div></div>
    {extra}<div class="fl-module-html"><div class="fl-html">
    <div class="panel panel-default"><div class="step-by-step-number">Step 1</div>
    <div class="row"><div class="step-by-step-image"><img src="https://www.scratchmagazine.co.uk/step.jpg"></div>
    <div>Paint the nail.</div></div></div></div></div></div>'''


class LuxuryAdapterTests(unittest.TestCase):
    def test_topic_and_quality_holds_are_not_recrawled(self):
        db=sqlite3.connect(':memory:'); db.execute('CREATE TABLE articles (id TEXT, state TEXT, reason TEXT)')
        store=SimpleNamespace(db=db)
        for idx,(state,reason,expected) in enumerate([('held','topic: halloween',True),('held','localization: invalid',True),('held','unsupported layout',False),('ready',None,True)]):
            url=f'https://www.scratchmagazine.co.uk/nail_art/test-{idx}/'
            db.execute('INSERT INTO articles VALUES (?,?,?)',(identity(url),state,reason))
            self.assertEqual(skip_known(store,url),expected)
        db.close()

    def test_curated_discovery_bounded_deduplicated(self):
        curated=CURATED_LUXURY_URLS[0]
        urls=include_curated_luxury([curated]+['https://example.com/'+str(i) for i in range(25)])
        self.assertEqual(urls[0],curated)
        self.assertEqual(urls.count(curated),1)
        self.assertEqual(len(urls),20)

    def test_observed_taxonomy_and_reject_unhandled_content(self):
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(headless=True)
            page=browser.new_page()
            try:
                for category in ('Step by Step','Autumn/Winter, Seasonal & Occasions, Step by Step, Themed'):
                    page.set_content(document(category),wait_until='domcontentloaded')
                    result=page.evaluate(EXTRACT)
                    self.assertEqual(len(result['media']),2)
                    self.assertEqual([b['type'] for b in result['blocks']],['image','html','html','image','html'])
                for category,extra in [('Unrecognized', ''),('Step by Step','<div class="fl-module-photo"><img src="https://example.com/extra.jpg"></div>')]:
                    page.set_content(document(category,extra),wait_until='domcontentloaded')
                    with self.assertRaises(Error): page.evaluate(EXTRACT)
            finally: browser.close()
