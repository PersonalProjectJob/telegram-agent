import copy
import hashlib
import json
import unittest
import socket
from unittest.mock import patch, MagicMock
import test_official_review_contract as fixture
from source_mirror.localization import digest
from source_mirror.official_native_mock import mock_request
from source_mirror.official_native_guard import (verify, readback, capture_outputs,
    verify_outputs, capture_live_outputs, verify_live_outputs)


class NativeGuardTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.ContractTests(); self.f.setUp()
        self.mapping={'Safety':'12345678-1234-1234-1234-123456789abc'}
        url='https://vlink-storages-prod.sgp1.cdn.digitaloceanspaces.com/example.png'
        self.image={'id':'image-id','imageUrl':url,'imageRelativePath':'/example.png','thumbnailUrl':url,'thumbnailRelativePath':'/example.png'}
        self.cover=dict(self.image,id='cover-id')
        self.actual=mock_request(self.f.payload,self.cover,[self.image],self.mapping)
        self.boundary={'status':'REVALIDATED_NOT_PUBLISHABLE','payload_sha256':hashlib.sha256(self.f.payload).hexdigest(),
                       'category_mapping_sha256':digest(self.mapping)}

    def check(self, actual=None, fetch=None, boundary=None):
        return verify(self.f.payload,boundary or self.boundary,self.mapping,self.cover,[self.image],
                      json.dumps(self.actual if actual is None else actual).encode(),fetch=fetch or (lambda _:self.f.media['figure-1']))

    def test_valid_fixture_still_cannot_publish(self):
        self.assertEqual(self.check()['status'],'NATIVE_SHAPE_VERIFIED_NOT_PUBLISHABLE')

    def test_changed_native_fields_or_receipts_rejected(self):
        for mutation in (lambda p:p['translations'][0].update(title='different'),
                         lambda p:p['translations'][0].update(content=p['translations'][0]['content'].replace('target="_blank"','target="_self"')),
                         lambda p:p['translations'][0].update(content=p['translations'][0]['content'].replace('https://www.cdc.gov/', 'https://attacker.example/')),
                         lambda p:p.update(categoryId='x'*36),lambda p:p.update(isPublic=False),
                         lambda p:p['coverImage'].update(imageRelativePath='/different'),
                         lambda p:p.update(images=[]),lambda p:p.update(extra='changed')):
            actual=copy.deepcopy(self.actual); mutation(actual)
            with self.assertRaises(ValueError): self.check(actual)

    def test_reencoded_or_missing_cdn_and_wrong_policy_hold(self):
        for value in (b'',b'reencoded'):
            with self.subTest(value=value), self.assertRaises(ValueError): self.check(fetch=lambda _:value)
        with self.assertRaises(ValueError): self.check(boundary=dict(self.boundary,category_mapping_sha256='a'*64))
        broken=dict(self.image,imageRelativePath='/wrong')
        actual=mock_request(self.f.payload,self.cover,[broken],self.mapping)
        with self.assertRaises(ValueError):
            verify(self.f.payload,self.boundary,self.mapping,self.cover,[broken],json.dumps(actual).encode(),fetch=lambda _:self.f.media['figure-1'])

    def test_json_types_duplicates_and_nonfinite_rejected(self):
        for field,value in [('isPublic',1),('isFeatured',0),('videoUrls',[1.0])]:
            actual=copy.deepcopy(self.actual); actual[field]=value
            with self.subTest(field=field), self.assertRaises(ValueError): self.check(actual)
        with self.assertRaises(ValueError):
            verify(self.f.payload,self.boundary,self.mapping,self.cover,[self.image],b'{"status":"Published","status":"Draft"}',fetch=lambda _:b'x')

    def test_readback_rejects_untrusted_url_and_private_dns(self):
        with self.assertRaises(ValueError): readback('https://example.com/image')
        with patch('source_mirror.official_native_guard.socket.getaddrinfo',return_value=[(socket.AF_INET,socket.SOCK_STREAM,0,'',('127.0.0.1',443))]):
            with self.assertRaisesRegex(ValueError,'non-public'): readback(self.image['imageUrl'])

    def test_readback_connects_only_to_checked_address_with_cdn_tls_name(self):
        addr=('93.184.216.34',443); raw=MagicMock(); context=MagicMock(); response=MagicMock()
        response.status=200; response.read.return_value=b'bytes'
        def request(conn,*args,**kwargs): conn.connect()
        with patch('source_mirror.official_native_guard.socket.getaddrinfo',return_value=[(socket.AF_INET,socket.SOCK_STREAM,0,'',addr)]) as dns, \
             patch('source_mirror.official_native_guard.socket.create_connection',return_value=raw) as connect, \
             patch('source_mirror.official_native_guard.ssl.create_default_context',return_value=context), \
             patch('source_mirror.official_native_guard.http.client.HTTPSConnection.request',new=request), \
             patch('source_mirror.official_native_guard.http.client.HTTPSConnection.getresponse',return_value=response):
            self.assertEqual(readback(self.image['imageUrl']),b'bytes')
        self.assertEqual(dns.call_count,1); connect.assert_called_once_with(addr,timeout=12)
        context.wrap_socket.assert_called_once_with(raw,server_hostname='vlink-storages-prod.sgp1.cdn.digitaloceanspaces.com')


class OutputBindingTests(unittest.TestCase):
    def setUp(self):
        NativeGuardTests.setUp(self)
        payload=json.loads(self.f.payload);source=payload['source'];media=payload['media'][0]
        self.policy={'version':'official-media-policy-v1','category_mapping':self.mapping,'entries':[
            {'source_id':source['id'],'source_url':source['url'],'geographic_scope':source['scope'],
             'image_sha256':media['sha256'],'credit':media['credit'],'jurisdiction':'WORLDWIDE',
             'valid_until_utc':'2030-01-01T00:00:00Z'}]}
        self.boundary['record_sha256']='a'*64
        self.image.update(thumbnailUrl='https://vlink-storages-prod.sgp1.cdn.digitaloceanspaces.com/thumb.png',thumbnailRelativePath='/thumb.png')
        self.cover=dict(self.image,id='cover-id')
        self.actual=mock_request(self.f.payload,self.cover,[self.image],self.mapping)
        self.outputs={self.image['imageUrl']:b're-encoded full output',self.image['thumbnailUrl']:b'different thumbnail output'}

    def capture(self,**kwargs):
        args={'fetch':lambda url:self.outputs[url]};args.update(kwargs)
        return capture_outputs(self.f.payload,self.boundary,self.mapping,self.cover,[self.image],self.f.media,self.policy,**args)

    def verify_bound(self,binding=None,request=None,fetch=None):
        value=self.capture() if binding is None else binding
        raw=json.dumps(value,sort_keys=True).encode()
        return verify_outputs(self.f.payload,self.boundary,self.mapping,self.cover,[self.image],self.f.media,self.policy,
                              raw,hashlib.sha256(raw).hexdigest(),json.dumps(self.actual if request is None else request).encode(),
                              fetch=fetch or (lambda url:self.outputs[url]))

    def test_original_and_reencoded_outputs_are_bound_separately_not_authorized(self):
        bound=self.capture()
        self.assertNotEqual(bound['uploads'][0]['original_sha256'],bound['uploads'][0]['outputs']['imageUrl']['sha256'])
        self.assertNotEqual(bound['uploads'][0]['outputs']['imageUrl']['sha256'],bound['uploads'][0]['outputs']['thumbnailUrl']['sha256'])
        self.assertEqual(bound['uploads'][0]['original_sha256'],hashlib.sha256(self.f.media['figure-1']).hexdigest())
        result=self.verify_bound(bound)
        self.assertEqual(result['status'],'NATIVE_OUTPUT_BOUND_NOT_PUBLISHABLE')
        self.assertIs(result['publication_enabled'],False)
        self.assertIs(result['native_upload_provenance_verified'],False)
        self.assertIn('native_upload_request_to_output_provenance_not_proven',result['blockers'])

    def test_changed_cdn_output_or_thumbnail_at_post_is_rejected(self):
        bound=self.capture()
        for url in self.outputs:
            with self.subTest(url=url),self.assertRaisesRegex(ValueError,'changed'):
                self.verify_bound(bound,fetch=lambda current:b'changed' if current==url else self.outputs[current])

    def test_tampered_receipt_path_binding_and_native_request_rejected(self):
        bound=self.capture()
        changed=copy.deepcopy(bound);changed['uploads'][0]['outputs']['imageUrl']['sha256']='f'*64
        with self.assertRaises(ValueError):self.verify_bound(changed)
        changed=copy.deepcopy(self.actual);changed['coverImage']['imageUrl']=self.image['thumbnailUrl']
        with self.assertRaises(ValueError):self.verify_bound(bound,request=changed)
        self.cover['imageRelativePath']='/wrong.png'
        with self.assertRaisesRegex(ValueError,'path mismatch'):self.capture()

    def test_original_policy_and_frozen_binding_hash_must_match(self):
        self.f.media['figure-1']=b'changed original'
        with self.assertRaisesRegex(ValueError,'original media hash'):self.capture()
        self.f.media['figure-1']=b'actual image'
        for key,value in [('source_id','untrusted-source'),('image_sha256','b'*64),('jurisdiction','US'),('valid_until_utc','2020-01-01T00:00:00Z')]:
            previous=copy.deepcopy(self.policy);self.policy['entries'][0][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):self.capture()
            self.policy=previous
        bound=self.capture();raw=json.dumps(bound).encode()
        with self.assertRaisesRegex(ValueError,'frozen output binding'):
            verify_outputs(self.f.payload,self.boundary,self.mapping,self.cover,[self.image],self.f.media,self.policy,
                           raw,'0'*64,json.dumps(self.actual).encode(),fetch=lambda url:self.outputs[url])

    def test_live_wrappers_never_accept_fake_boundary_fetch_or_policy(self):
        for keyword,value in [('boundary',self.boundary),('fetch',lambda _:b'x'),('media_policy',self.policy)]:
            with self.subTest(keyword=keyword),self.assertRaises(TypeError):
                capture_live_outputs('/root','/evidence','a'*64,self.cover,[self.image],**{keyword:value})
            with self.subTest(keyword=keyword),self.assertRaises(TypeError):
                verify_live_outputs('/root','/evidence','a'*64,self.cover,[self.image],b'{}','a'*64,b'{}',**{keyword:value})
        with patch('source_mirror.official_auto_boundary.validate_live_boundary',side_effect=ValueError('trusted .gov boundary held')) as validate:
            with self.assertRaisesRegex(ValueError,'trusted .gov boundary held'):
                capture_live_outputs('/root','/evidence','a'*64,self.cover,[self.image])
            validate.assert_called_once_with('/root','/evidence','a'*64)

    def test_same_cdn_url_cannot_bind_different_originals(self):
        payload=json.loads(self.f.payload)
        other=dict(payload['media'][0],id='figure-2',sha256=hashlib.sha256(b'second original').hexdigest())
        payload['media'].append(other);raw=json.dumps(payload).encode();boundary=dict(self.boundary,payload_sha256=hashlib.sha256(raw).hexdigest())
        self.policy['entries'].append(dict(self.policy['entries'][0],image_sha256=other['sha256']))
        gallery=[self.image,dict(self.image,id='second-upload')]
        originals=dict(self.f.media,**{'figure-2':b'second original'})
        with self.assertRaisesRegex(ValueError,'reused across different originals'):
            capture_outputs(raw,boundary,self.mapping,self.cover,gallery,originals,self.policy,fetch=lambda url:self.outputs[url])


if __name__=='__main__': unittest.main()
