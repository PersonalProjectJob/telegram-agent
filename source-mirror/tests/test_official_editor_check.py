import copy
import hashlib
import json
import unittest
from source_mirror.official_editor_check import check

class EditorCheckTests(unittest.TestCase):
    def setUp(self):
        self.body='<p>Source <a href="https://www.cdc.gov/example" target="_blank" rel="noopener noreferrer">CDC</a></p>'
        self.payload=json.dumps({'locales':{'vi':{'body_html':self.body},'en':{'body_html':self.body}}}).encode()
        self.boundary={'status':'REVALIDATED_NOT_PUBLISHABLE','payload_sha256':hashlib.sha256(self.payload).hexdigest()}
        self.editors={'vi':self.body,'en':self.body}
    def test_match_still_does_not_authorize_publish(self):
        self.assertEqual(check(self.payload,self.boundary,self.editors)['status'],'EDITOR_VERIFIED_NOT_PUBLISHABLE')
    def test_changed_links_text_and_boundary_are_held(self):
        for before,after in (('https://www.cdc.gov/example','https://evil.example/'),('target="_blank"','target="_self"'),
                             ('rel="noopener noreferrer"',''),('CDC','changed')):
            changed=copy.deepcopy(self.editors); changed['vi']=changed['vi'].replace(before,after)
            with self.subTest(before=before), self.assertRaises(ValueError): check(self.payload,self.boundary,changed)
        with self.assertRaises(ValueError): check(self.payload+b' ',self.boundary,self.editors)
        with self.assertRaises(ValueError): check(self.payload,{'status':'PASS'},self.editors)
