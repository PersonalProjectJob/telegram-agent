import hashlib
import tempfile
import unittest
from pathlib import Path
from source_mirror.official_source_meaning import fingerprint,differs_from_held
from source_mirror.official_sources import SOURCES,collect

class SourceMeaningTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.item=next(x for x in SOURCES if x['id']=='irs-tip-reporting')
        self.html=b'<html><head><title>Tips</title><script>counter1</script></head><body><nav>menu</nav><main><p>Read <a href="https://www.irs.gov/a">the official rule</a> today.</p></main></body></html>'
    def save(self,data):
        collect(self.root,self.item,lambda _:data)
        return hashlib.sha256(data).hexdigest()
    def test_script_and_menu_change_do_not_unlock_held_content(self):
        old=self.save(self.html);new=self.save(self.html.replace(b'counter1',b'counter2').replace(b'menu',b'menu updated'))
        self.assertNotEqual(old,new);self.assertEqual(fingerprint(self.root,self.item['id'],old),fingerprint(self.root,self.item['id'],new))
        self.assertFalse(differs_from_held(self.root,self.item['id'],new,[old]))
    def test_changed_text_link_position_or_title_changes_bound_content(self):
        old=self.save(self.html)
        variants=[self.html.replace(b' today.',b' tomorrow.'),self.html.replace(b'irs.gov/a',b'irs.gov/b'),
                  self.html.replace(b'<title>Tips</title>',b'<title>Updated tips</title>'),
                  self.html.replace(b'Read <a',b'Read carefully <a')]
        for value in variants:
            sha=self.save(value)
            with self.subTest(sha=sha):self.assertTrue(differs_from_held(self.root,self.item['id'],sha,[old]))
    def test_broken_held_snapshot_fails_closed(self):
        old=self.save(self.html);new=self.save(self.html.replace(b' today.',b' tomorrow.'))
        (self.root/'official-review-queue'/self.item['id']/old/'source.html').write_bytes(b'tampered')
        with self.assertRaises(Exception):differs_from_held(self.root,self.item['id'],new,[old])
