import unittest
from source_mirror.official_source_diff import compare
from source_mirror.official_sources import SOURCES

class RedactedDiffTests(unittest.TestCase):
    def test_script_change_isolated_and_does_not_prove_freshness(self):
        old=b'<html><head><title>Tips</title><script>SECRET_ONE</script></head><body><main><p>Guidance.</p></main></body></html>'
        new=old.replace(b'SECRET_ONE',b'SECRET_TWO')
        result=compare(SOURCES[0],old,new)
        self.assertFalse(result['bytes_equal']);self.assertTrue(result['reference_equal']);self.assertTrue(result['ordered_links_equal'])
        self.assertFalse(result['regions_equal']['scripts']);self.assertTrue(result['regions_equal']['main'])
        self.assertNotIn('SECRET',str(result));self.assertIn('NOT_FRESHNESS_PROOF',result['status'])
    def test_changed_link_detected_without_disclosing_url(self):
        old=b'<html><head><title>Tips</title></head><body><main><p><a href="https://example.com/a">Guidance</a></p></main></body></html>'
        result=compare(SOURCES[0],old,old.replace(b'example.com/a',b'example.com/b'))
        self.assertFalse(result['ordered_links_equal']);self.assertNotIn('example.com',str(result))
