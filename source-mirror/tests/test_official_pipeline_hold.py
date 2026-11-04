"""Offline integration: valid-looking evidence cannot bypass real reservation."""
import copy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import Mock

import test_official_auto_evidence as evidence_fixture
from source_mirror.official_auto_boundary import validate_at_boundary
from source_mirror.official_native_guard import verify
from source_mirror.official_native_mock import mock_request
from source_mirror.snapshot import save
from source_mirror.store import Store


class OfficialPipelineHoldTests(unittest.TestCase):
    def setUp(self):
        self.fixture=evidence_fixture.EvidenceTests(); self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        self.capture=self.fixture.capture(); self.folder=Path(self.capture['path'])
        self.store=Store(self.fixture.base.root); self.addCleanup(self.store.close)
        self.store.set_paused(False)
        self.source=self.fixture.fixture.item['url']
        meta={'source_url':self.source,'title':'Official fixture'}
        self.aid=save(self.store.root,meta,[{'type':'paragraph','text':'Fixture'}],'<p>Fixture</p>',[])
        self.store.add_ready(self.source,meta['title'])
        self.evaluator=Mock(return_value=copy.deepcopy(self.fixture.result))

    def boundary(self):
        return validate_at_boundary(self.store.root,self.folder,self.capture['record_sha256'],self.fixture.fixture.cfg,
                                    evaluator=self.evaluator,post_refetch=lambda _:self.fixture.fixture.source,
                                    policy_loader=lambda:self.fixture.result['media_policy'])

    def test_offline_evidence_and_native_comparison_do_not_authorize_reservation(self):
        boundary=self.boundary(); payload=(self.folder/'payload.json').read_bytes()
        mapping=self.fixture.result['media_policy']['category_mapping']
        url='https://vlink-storages-prod.sgp1.cdn.digitaloceanspaces.com/test.png'
        image={'id':'mock-id','imageUrl':url,'imageRelativePath':'/test.png','thumbnailUrl':url,'thumbnailRelativePath':'/test.png'}
        request=mock_request(payload,image,[image],mapping)
        result=verify(payload,boundary,mapping,image,[image],json.dumps(request).encode(),fetch=lambda _:self.fixture.fixture.image)
        self.assertEqual(result['status'],'NATIVE_SHAPE_VERIFIED_NOT_PUBLISHABLE')
        with self.assertRaisesRegex(ValueError,'official source'):
            self.store.reserve('2026-11-01','08:00',self.aid)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM attempts').fetchone()[0],0)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM slots').fetchone()[0],0)
        # The blocked official article has not consumed a slot or interrupted
        # the ordinary reservation path.
        url='https://example.com/scratch-fixture'; meta={'source_url':url,'title':'Scratch fixture'}
        aid=save(self.store.root,meta,[{'type':'paragraph','text':'Fixture'}],'<p>Fixture</p>',[])
        self.store.add_ready(url,meta['title'])
        attempt,_=self.store.reserve('2026-11-01','08:00',aid)
        self.assertIsInstance(attempt,int)

    def test_changed_evidence_never_reaches_reservation_or_guard(self):
        (self.folder/'payload.json').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'file changed'): self.boundary()
        self.evaluator.assert_not_called()
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM attempts').fetchone()[0],0)
