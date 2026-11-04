import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock,patch
from source_mirror.official_create_observer import capture_aborted
from source_mirror.official_upload_controller import UploadObserver

class CreateObserverTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.root=Path(self.tmp.name)
        self.folder=self.root/'evidence'; self.folder.mkdir(); self.sha='a'*64; self.image=b'\xff\xd8\xffimage'
        self.media={'id':'figure','sha256':hashlib.sha256(self.image).hexdigest()}
        locale={'title':'Title','summary':'Summary','body_html':'<p>Body</p>','meta_title':'Meta','meta_description':'Description'}
        self.payload={'source':{'url':'https://www.cdc.gov/example'},'media':[self.media],'locales':{'vi':locale,'en':locale},
                      'category':'Safety','status':'Published','published_at':'2026-11-01T08:00:00+07:00',
                      'is_public':True,'is_featured':False,'video_urls':[],'hashtags':['a','b','c']}
        self.payload_bytes=json.dumps(self.payload).encode(); self.mapping={'Safety':'12345678-1234-1234-1234-123456789abc'}
        self.values=(self.payload_bytes,{'status':'REVALIDATED_NOT_PUBLISHABLE'},self.mapping,{'figure':self.image},{'version':'trusted'})
        self.patches=[patch('source_mirror.official_upload_controller._live_inputs',return_value=self.values),
                      patch('source_mirror.official_create_observer._live_inputs',return_value=self.values),
                      patch('source_mirror.official_upload_controller._sync_directory_chain'),
                      patch('source_mirror.official_create_observer._sync_directory_chain')]
        for p in self.patches:p.start();self.addCleanup(p.stop)
        self.cover,self.cover_receipt=self.upload('cover','cover.jpg');self.gallery,self.gallery_receipt=self.upload('gallery','gallery.jpg')
        vi={'languageCode':'VI','title':'Title','summary':'Summary','content':'<p>Body</p>','metaTitle':'Meta','metaDescription':'Description'}
        en=dict(vi,languageCode='EN')
        self.request={'categoryId':self.mapping['Safety'],'status':'Published','publishedAt':'2026-11-01T01:00:00.000Z',
                      'isPublic':True,'isFeatured':False,'coverImage':dict(self.cover_receipt,id='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'),
                      'images':[dict(self.gallery_receipt,id='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb')],
                      'videoUrls':[],'hashtags':['a','b','c'],'externalSourceUrl':self.payload['source']['url'],'translations':[vi,en]}
    def upload(self,role,name):
        observer=UploadObserver(self.root,self.folder,self.sha);observer.arm('figure',role)
        request=MagicMock();request.method='POST';request.url='https://api.nailhub.ai/api/v1/Images/quick-upload'
        request.headers={'content-type':'multipart/form-data; boundary=x'}
        request.post_data_buffer=(b'--x\r\nContent-Disposition: form-data; name="file"; filename="x.jpg"\r\nContent-Type: image/jpeg\r\n\r\n'+self.image+b'\r\n--x--\r\n')
        route=MagicMock();route.request=request;observer.route(route)
        receipt={'imageUrl':'https://vlink-storages-prod.sgp1.cdn.digitaloceanspaces.com/'+name,'imageRelativePath':'/'+name,'thumbnailUrl':'','thumbnailRelativePath':''}
        raw=json.dumps(receipt).encode();response=MagicMock();response.request=request;response.status=200;response.headers={'content-length':str(len(raw))};response.body.return_value=raw
        observer.response(response);return observer,receipt
    def call(self,value):
        route=MagicMock();route.request.method='POST';route.request.url='https://api.nailhub.ai/api/v1/admin/blogs';route.request.post_data_buffer=json.dumps(value).encode()
        try:return capture_aborted(route,self.root,self.folder,self.sha,self.cover,[self.gallery])
        finally:route.abort.assert_called_once_with('blockedbyclient');route.continue_.assert_not_called()
    def test_exact_observed_receipts_and_ui_generated_ids_produce_only_canary_evidence(self):
        result=self.call(self.request)
        self.assertEqual(result['status'],'CANARY_ONLY_NOT_PUBLISHABLE');self.assertTrue(Path(result['path']).is_file())
    def test_receipt_id_content_and_json_type_mutations_are_held_after_abort(self):
        for field in ('url','id','thumbnail','content','bool'):
            value=copy.deepcopy(self.request)
            if field=='url':value['images'][0]['imageUrl']='https://example.com/changed'
            if field=='id':value['images'][0]['id']='made-up'
            if field=='thumbnail':value['images'][0]['thumbnailUrl']='https://example.com/thumb'
            if field=='content':value['translations'][0]['content']='<p>changed</p>'
            if field=='bool':value['isPublic']=1
            with self.subTest(field=field), self.assertRaises(ValueError):self.call(value)
