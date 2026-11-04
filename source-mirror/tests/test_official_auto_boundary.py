import copy
from pathlib import Path
import unittest
from unittest.mock import Mock
import test_official_auto_evidence as fixture
from source_mirror.official_auto_boundary import validate_at_boundary
from source_mirror.official_auto_eligibility import SourceChanged
from source_mirror.official_native_mock import mock_request, mock_route
from source_mirror.official_payload import build


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixture.EvidenceTests(); self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        self.capture=self.fixture.capture(); self.folder=Path(self.capture['path'])
        self.evaluator=Mock(return_value=copy.deepcopy(self.fixture.result))

    def boundary(self):
        return validate_at_boundary(self.fixture.base.root,self.folder,self.capture['record_sha256'],
                                    self.fixture.fixture.cfg,evaluator=self.evaluator,
                                    post_refetch=lambda _:self.fixture.fixture.source,
                                    policy_loader=lambda:self.fixture.result['media_policy'])

    def test_both_boundaries_revalidate_without_any_network_mock_create(self):
        self.assertEqual(self.boundary()['status'],'REVALIDATED_NOT_PUBLISHABLE')
        f=self.fixture.fixture; payload=build(f.draft,f.base.media,f.base.editorial)
        url='https://vlink-storages-prod.sgp1.cdn.digitaloceanspaces.com/test.png'
        image={'id':'mock-id','imageUrl':url,'imageRelativePath':'/test.png','thumbnailUrl':url,'thumbnailRelativePath':'/test.png'}
        assets={url:f.image}; mapping={'Safety':'12345678-1234-1234-1234-123456789abc'}
        request=mock_request(payload,image,[image],mapping)
        self.assertEqual(mock_route(payload,image,[image],mapping,assets,copy.deepcopy(request),self.boundary),'MOCK_ONLY')
        self.assertEqual(self.evaluator.call_count,2)
        changed=copy.deepcopy(request); changed['translations'][0]['title']='changed'
        with self.assertRaises(ValueError): mock_route(payload,image,[image],mapping,assets,changed,self.boundary)
        with self.assertRaises(ValueError): mock_route(payload,image,[image],mapping,assets,{'unrelated':'data'},self.boundary)
        with self.assertRaises(ValueError): mock_route(payload,image,[image],mapping,{url:b'changed'},request,self.boundary)
        with self.assertRaises(ValueError): mock_route(payload,image,[image],{'Safety':'12345678-1234-1234-1234-123456789abd'},assets,request,self.boundary)

    def test_changed_policy_or_media_never_reaches_mock_create(self):
        self.evaluator.return_value['media_policy_sha256']='a'*64
        with self.assertRaisesRegex(ValueError,'fresh boundary evidence differs'): self.boundary()
        self.evaluator.return_value=copy.deepcopy(self.fixture.result)
        (self.folder/'media'/'01').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'file changed'): self.boundary()

    def test_media_change_during_inference_is_rejected(self):
        def mutate(*args,**kwargs):
            (self.folder/'media'/'01').write_bytes(b'changed while evaluating')
            return copy.deepcopy(self.fixture.result)
        self.evaluator.side_effect=mutate
        with self.assertRaisesRegex(ValueError,'changed during evaluation'): self.boundary()

    def test_source_and_policy_change_after_inference_is_rejected(self):
        args=(self.fixture.base.root,self.folder,self.capture['record_sha256'],self.fixture.fixture.cfg)
        with self.assertRaisesRegex(SourceChanged,'source changed during evaluation'):
            validate_at_boundary(*args,evaluator=self.evaluator,post_refetch=lambda _:b'changed',
                                 policy_loader=lambda:self.fixture.result['media_policy'])
        with self.assertRaisesRegex(ValueError,'source unavailable after evaluation') as unavailable:
            validate_at_boundary(*args,evaluator=self.evaluator,post_refetch=lambda _:None,
                                 policy_loader=lambda:self.fixture.result['media_policy'])
        self.assertNotIsInstance(unavailable.exception,SourceChanged)
        with self.assertRaisesRegex(ValueError,'policy changed during evaluation'):
            validate_at_boundary(*args,evaluator=self.evaluator,post_refetch=lambda _:self.fixture.fixture.source,
                                 policy_loader=lambda:{'version':'changed','entries':[]})


if __name__=='__main__': unittest.main()
