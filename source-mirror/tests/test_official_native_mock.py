import copy
import unittest
import test_official_review_contract as fixture
from source_mirror.official_native_mock import mock_request, mock_guard


class NativeMockTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixture.ContractTests(); self.fixture.setUp()
        url='https://vlink-storages-prod.sgp1.cdn.digitaloceanspaces.com/example.png'
        self.image={'id':'mock-id','imageUrl':url,'imageRelativePath':'/example.png','thumbnailUrl':url,'thumbnailRelativePath':'/example.png'}
        self.category='12345678-1234-1234-1234-123456789abc'
        self.mapping={'Safety':self.category}
        self.expected=mock_request(self.fixture.payload,self.image,[self.image],self.mapping)

    def test_mock_never_calls_network_and_rejects_changed_fields(self):
        self.assertEqual(set(self.expected),{'categoryId','status','publishedAt','isPublic','isFeatured','coverImage','images','videoUrls','hashtags','externalSourceUrl','translations'})
        self.assertEqual(mock_guard(self.expected,copy.deepcopy(self.expected)),'MOCK_ONLY')
        for key,value in [('externalSourceUrl','https://example.com/'),('status','Draft'),('isPublic',False),
                          ('coverImage','https://example.com/image'),('hashtags',['different']),('images',[]),
                          ('categoryId','changed'),('isFeatured',True),('videoUrls',['https://example.com'])]:
            actual=copy.deepcopy(self.expected); actual[key]=value
            with self.subTest(key=key), self.assertRaises(ValueError): mock_guard(self.expected,actual)
        for key in ('title','summary','metaTitle','metaDescription','content'):
            actual=copy.deepcopy(self.expected); actual['translations'][0][key]='changed'
            with self.subTest(key=key), self.assertRaises(ValueError): mock_guard(self.expected,actual)
        actual=copy.deepcopy(self.expected); actual['unexpected']='changed'
        with self.assertRaises(ValueError): mock_guard(self.expected,actual)

    def test_media_mapping_rejects_untrusted_url(self):
        bad=dict(self.image,imageUrl='https://example.com/asset')
        with self.assertRaises(ValueError): mock_request(self.fixture.payload,bad,[bad],self.mapping)
        with self.assertRaises(ValueError): mock_request(self.fixture.payload,self.image,[self.image],{'Safety':'x'*36})
        with self.assertRaises(ValueError): mock_request(self.fixture.payload,self.image,[self.image],{'other':self.category})

    def test_thumbnail_fields_must_both_be_empty_or_both_present(self):
        empty=dict(self.image,thumbnailUrl='',thumbnailRelativePath='')
        self.assertEqual(mock_request(self.fixture.payload,empty,[empty],self.mapping)['coverImage'],empty)
        for item in (dict(empty,thumbnailUrl=self.image['thumbnailUrl']),dict(empty,thumbnailRelativePath='/thumb')):
            with self.assertRaises(ValueError):mock_request(self.fixture.payload,item,[item],self.mapping)

    def test_nested_json_number_type_mismatch_rejected(self):
        with self.assertRaises(ValueError): mock_guard({'nested':{'value':1}},{'nested':{'value':1.0}})
        with self.assertRaises(ValueError): mock_guard({'nested':{'value':False}},{'nested':{'value':0}})


if __name__=='__main__': unittest.main()
