import copy
import hashlib
import json
import unittest
from unittest.mock import patch
import test_official_auto_gate as fixture
from source_mirror.official_prepare import prepare
from source_mirror.official_payload import build
from source_mirror.official_sources import SOURCES


class OfficialPrepareTests(unittest.TestCase):
    def setUp(self):
        self.base=fixture.AutomaticGateTests(); self.base.setUp(); self.addCleanup(self.base.doCleanups)
        self.item=SOURCES[0]; self.sha=self.base.sha; self.media=self.base.media; self.editorial=self.base.editorial
        self.images={'figure':b'fixture image'}; self.rights={'figure':b'fixture rights'}

    def run_prepare(self,draft=None,source=None,sha=None):
        return prepare(self.base.root,source or self.item['id'],sha or self.sha,
                       self.base.draft if draft is None else draft,self.editorial,self.media,self.images,self.rights)

    def test_payload_is_canonical_and_trusted_capture_remains_not_publishable(self):
        expected={'status':'ELIGIBLE_FOR_NATIVE_VERIFICATION_ONLY','publication_enabled':False,'path':'fixture','record_sha256':'b'*64}
        with patch('source_mirror.official_prepare.capture_live',return_value=expected) as capture:
            self.assertEqual(self.run_prepare(),expected)
            args=capture.call_args.args
            self.assertEqual(args[4],build(self.base.draft,self.media,self.editorial))
            self.assertEqual(args[1:3],(self.item['id'],self.sha))
            self.assertEqual(args[7],self.images); self.assertEqual(args[8],self.rights)

    def test_wrong_source_or_changed_content_never_reaches_capture(self):
        with patch('source_mirror.official_prepare.capture_live') as capture:
            for kwargs in ({'source':'unknown'}, {'sha':'f'*64}, {'draft':b'{}'}, {'draft':b'bad'}):
                with self.subTest(kwargs=kwargs), self.assertRaises(ValueError): self.run_prepare(**kwargs)
            capture.assert_not_called()

    def test_incomplete_or_authorizing_capture_is_rejected(self):
        for result in ({}, {'status':'HOLD','publication_enabled':False},
                       {'status':'ELIGIBLE_FOR_NATIVE_VERIFICATION_ONLY','publication_enabled':True}):
            with self.subTest(result=result), patch('source_mirror.official_prepare.capture_live',return_value=result), self.assertRaises(ValueError):
                self.run_prepare()
