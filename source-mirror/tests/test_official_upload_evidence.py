import hashlib
import json
import unittest
from source_mirror.official_upload_evidence import bind

class UploadEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.image=b'\xff\xd8\xfffixture jpeg bytes'
        self.content_type='multipart/form-data; boundary=test-boundary'
        self.request=(b'--test-boundary\r\nContent-Disposition: form-data; name="file"; filename="test.jpg"\r\n'
                      b'Content-Type: image/jpeg\r\n\r\n'+self.image+b'\r\n--test-boundary--\r\n')
        self.response={'imageUrl':'https://vlink-storages-prod.sgp1.cdn.digitaloceanspaces.com/a.jpg',
                       'imageRelativePath':'/a.jpg','thumbnailUrl':'relative-thumb','thumbnailRelativePath':'relative-thumb'}

    def test_exact_file_and_observed_response_are_bound_without_thumbnail_claim(self):
        record=json.loads(bind(self.request,self.content_type,self.image,self.response))
        self.assertEqual(record['status'],'UPLOAD_EVIDENCE_NOT_PUBLISHABLE')
        self.assertEqual(record['source_sha256'],hashlib.sha256(self.image).hexdigest())
        self.assertEqual(record['request_sha256'],hashlib.sha256(self.request).hexdigest())
        self.assertEqual(record['response'],self.response)

    def test_changed_bytes_extra_parts_headers_and_unsafe_response_hold(self):
        cases=[(self.request,self.content_type,self.image+b'x',self.response),
               (self.request.replace(b'name="file"',b'name="wrong"'),self.content_type,self.image,self.response),
               (self.request.replace(b'Content-Type: image/jpeg',b'Content-Type: image/jpeg\r\nContent-Transfer-Encoding: base64'),self.content_type,self.image,self.response),
               (self.request.replace(b'--test-boundary--',b'--test-boundary\r\nContent-Disposition: form-data; name="extra"\r\n\r\nx\r\n--test-boundary--'),self.content_type,self.image,self.response),
               (self.request,self.content_type+'\r\nX: evil',self.image,self.response),
               (self.request,self.content_type,self.image,dict(self.response,imageUrl='https://evil.example/a.jpg')),
               (self.request,self.content_type,self.image,dict(self.response,imageUrl='https://vlink-storages-prod.sgp1.cdn.digitaloceanspaces.com/other/thenailhub/a.jpg')),
               (self.request,self.content_type,self.image,dict(self.response,imageRelativePath='/other.jpg'))]
        for args in cases:
            with self.subTest(case=args[1]), self.assertRaises(ValueError): bind(*args)

    def test_production_bucket_prefix_is_accepted_exactly(self):
        response=dict(self.response,imageUrl='https://vlink-storages-prod.sgp1.cdn.digitaloceanspaces.com/vlink-storages-prod/thenailhub/posting/a.jpg',imageRelativePath='posting/a.jpg')
        self.assertEqual(json.loads(bind(self.request,self.content_type,self.image,response))['response'],response)
