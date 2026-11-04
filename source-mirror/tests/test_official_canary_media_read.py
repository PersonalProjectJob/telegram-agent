import unittest
from source_mirror.official_create_canary import observed_media_read
from source_mirror.official_upload_controller import UploadObserver
from source_mirror.official_upload_evidence import CDN


class CanaryMediaReadTests(unittest.TestCase):
    def test_exact_verified_receipt_urls_only_and_no_writes(self):
        image='https://'+CDN+'/images/one.jpg';thumbnail='https://'+CDN+'/thumbnails/one.jpg'
        observer=object.__new__(UploadObserver);observer.closed=True;observer.record_sha='a'*64
        observer.observation={'status':'CANARY_ONLY_NOT_PUBLISHABLE','record_sha256':observer.record_sha,
                              'binding':{'response':{'imageUrl':image,'thumbnailUrl':thumbnail}}}
        for method in ('GET','HEAD'):
            for url in (image,thumbnail):self.assertTrue(observed_media_read(method,url,[None,observer]))
        for method,url in [('POST',image),('PUT',image),('DELETE',image),('GET',image+'?x=1'),
                           ('GET',image+'#fragment'),('GET','https://'+CDN+'/images/other.jpg'),
                           ('GET',image.replace('https:','http:')),('GET',image.replace(CDN,'evil.example')),
                           ('GET',image.replace(CDN,'user@'+CDN))]:
            with self.subTest(method=method,url=url):self.assertFalse(observed_media_read(method,url,[observer]))
        self.assertFalse(observed_media_read('GET',image,[observer.observation]))
        observer.closed=False;self.assertFalse(observed_media_read('GET',image,[observer]))
        observer.closed=True;observer.observation['record_sha256']='b'*64
        self.assertFalse(observed_media_read('GET',image,[observer]))


if __name__=='__main__':unittest.main()
