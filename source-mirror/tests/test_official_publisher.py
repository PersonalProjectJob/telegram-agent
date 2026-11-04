"""Offline readback checks; these do not establish the live admin schema."""
import copy
import json
import unittest
from unittest.mock import MagicMock,patch
from source_mirror.official_publisher import available,publish,verify_readback

class PublisherTests(unittest.TestCase):
    def setUp(self):
        body='<p>Source <a href="https://www.cdc.gov/a" target="_blank" rel="noopener noreferrer">CDC</a></p>'
        locale={'title':'Title','summary':'Summary','body_html':body,'meta_title':'Meta','meta_description':'Description'}
        self.payload={'source':{'url':'https://www.cdc.gov/a'},'locales':{'vi':locale,'en':locale},'category':'Safety',
                      'hashtags':['a','b','c'],'published_at':'2026-11-01T08:00:00+07:00',
                      'status':'Published','is_public':True,'is_featured':False,'video_urls':[]}
        self.raw=json.dumps(self.payload).encode();self.mapping={'Safety':'12345678-1234-1234-1234-123456789abc'}
        self.cover={'imageUrl':'https://vlink-storages-prod.sgp1.cdn.digitaloceanspaces.com/cover.jpg'}
        self.gallery=[{'imageUrl':'https://vlink-storages-prod.sgp1.cdn.digitaloceanspaces.com/gallery.jpg'}]
        self.request={'coverImage':self.cover,'images':self.gallery}
        vi={'languageCode':'Vi','title':'Title','summary':'Summary','content':body,'metaTitle':'Meta','metaDescription':'Description'}
        self.record={'externalSourceUrl':self.payload['source']['url'],'translations':[vi,dict(vi,languageCode='En')],
                     'status':'Published','isPublic':True,'isFeatured':False,'videoUrls':[],'categoryId':self.mapping['Safety'],'hashtags':['a','b','c'],
                     'coverImage':self.cover,'images':self.gallery,'publishedAt':'2026-11-01T01:00:00+00:00','slug':'fixture'}
        self.page=MagicMock();response=MagicMock();response.ok=True
        response.body.side_effect=lambda:json.dumps({'items':[self.record]}).encode()
        self.page.expect_response.return_value.__enter__.return_value.value=response
        rows=self.page.locator.return_value;rows.count.return_value=1;rows.first.inner_text.return_value='Published'
        self.playwright=MagicMock();public=self.playwright.chromium.launch.return_value.new_context.return_value.new_page.return_value
        public.url='https://nailhub.ai/blog/fixture';public.goto.return_value.status=200
        public.locator.return_value.count.return_value=1;public.locator.return_value.inner_text.return_value='Title'
        public.locator.return_value.evaluate_all.return_value=[self.cover['imageUrl'],self.gallery[0]['imageUrl']]
        self.body=MagicMock();self.body.inner_html.return_value=body
    def check(self):
        with patch('source_mirror.official_publisher.admin_content',return_value=self.body),patch('source_mirror.official_publisher.guest_content',return_value=self.body):
            return verify_readback(self.page,self.playwright,self.raw,self.request,self.mapping,'2026-11-01')
    def test_default_writer_is_disabled(self):
        self.assertFalse(available())
        with self.assertRaises(ValueError):publish(MagicMock(),{},1)
    def test_unique_exact_admin_and_guest_fixture_verifies(self):
        self.assertEqual(self.check(),{'verified':True,'url':'https://nailhub.ai/blog/fixture','published_day':'2026-11-01'})
    def test_wrong_source_content_media_metadata_or_date_hold(self):
        original=copy.deepcopy(self.record)
        for field in ('source','content','media','category','date','public','featured','video','summary','meta'):
            self.record=copy.deepcopy(original)
            if field=='source':self.record['externalSourceUrl']='https://evil.example/'
            if field=='content':self.record['translations'][0]['content']='<p>changed</p>'
            if field=='media':self.record['images'][0]['imageUrl']='https://evil.example/x'
            if field=='category':self.record['categoryId']='wrong'
            if field=='date':self.record['publishedAt']='2026-11-01T02:00:00+00:00'
            if field=='public':self.record['isPublic']=1
            if field=='featured':self.record['isFeatured']=0
            if field=='video':self.record['videoUrls']=['https://example.com/video']
            if field=='summary':self.record['translations'][1]['summary']='changed'
            if field=='meta':self.record['translations'][0]['metaTitle']='changed'
            with self.subTest(field=field),self.assertRaises(ValueError):self.check()
