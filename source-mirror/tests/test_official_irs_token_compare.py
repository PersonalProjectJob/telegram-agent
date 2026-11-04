import unittest
from source_mirror.official_irs_token_compare import compare

class TokenComparisonTests(unittest.TestCase):
    def setUp(self):
        self.old=(b'<html><head><title>IRS</title><script type="application/json" data-drupal-selector="drupal-settings-json">'
                  b'{"ajaxPageState":{"theme_token":"'+b'A'*43+b'","theme":"irs"}}</script></head>'
                  b'<body><main><p>Read <a href="https://www.irs.gov/a">guidance</a>.</p></main></body></html>')
        self.new=self.old.replace(b'A'*43,b'B'*43)
    def test_only_named_token_may_differ(self):
        result=compare(self.old,self.new)
        self.assertEqual(result['status'],'TOKEN_ONLY_PROTOTYPE_NOT_PUBLISHABLE')
        self.assertNotIn('A'*43,str(result));self.assertNotIn('B'*43,str(result))
    def test_any_other_byte_or_script_identity_change_holds(self):
        changes=[self.new.replace(b'guidance',b'advice'),self.new.replace(b'irs.gov/a',b'irs.gov/b'),
                 self.new.replace(b'"theme":"irs"',b'"theme":"other"'),
                 self.new.replace(b'drupal-settings-json',b'other-settings'),self.new.replace(b'B'*43,b'B'*42),
                 self.new.replace(b'</main>',b'<p>added</p></main>'),
                 self.new.replace(b'"theme":"irs"',b'"theme_token":"'+b'B'*43+b'"')]
        for changed in changes:
            with self.subTest(changed=changed[:20]),self.assertRaises(ValueError):compare(self.old,changed)
    def test_escaped_target_key_cannot_normalize_another_token(self):
        body=(b'{"ajaxPageState":{"theme_\\u0074oken":"'+b'A'*43+b'"},'
              b'"other":{"theme_token":"'+b'A'*43+b'"}}')
        old=(b'<html><head><title>IRS</title><script type="application/json" data-drupal-selector="drupal-settings-json">'+body+
             b'</script></head><body><main><p>Guidance.</p></main></body></html>')
        changed=old.replace(b'"other":{"theme_token":"'+b'A'*43,b'"other":{"theme_token":"'+b'B'*43)
        with self.assertRaises(ValueError):compare(old,changed)
