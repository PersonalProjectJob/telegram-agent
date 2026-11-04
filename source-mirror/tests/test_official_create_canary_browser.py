"""Local Chromium UI fixture; trusted source/policy is explicitly mocked."""
import hashlib
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
import test_official_auto_gate as fixture
from source_mirror.official_payload import build
from source_mirror.official_create_canary import run
from source_mirror.official_upload_response_stream import UploadResponseStream


class BrowserFixtureTests(unittest.TestCase):
    def _run_case(self,mutation=None):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError: self.skipTest('Playwright unavailable')
        base=fixture.AutomaticGateTests();base.setUp();self.addCleanup(base.doCleanups)
        image=b'\xff\xd8\xfffixture image';base.media[0]['sha256']=hashlib.sha256(image).hexdigest()
        payload=build(base.draft,base.media,base.editorial); data=json.loads(payload)
        record={'draft_sha256':hashlib.sha256(base.draft).hexdigest(),'editorial':base.editorial,'media_manifest':base.media}
        record_bytes=json.dumps(record).encode();sha=hashlib.sha256(record_bytes).hexdigest()
        folder=base.root/'official-auto-evidence'/'fixture'/'snapshot'/sha;folder.mkdir(parents=True)
        (folder/'record.json').write_bytes(record_bytes);(folder/'draft.json').write_bytes(base.draft)
        (base.root/'browser'/'nailhub-profile').mkdir(parents=True)
        policy={'category_mapping':{'Safety':'12345678-1234-1234-1234-123456789abc'}}
        frozen=(payload,{'status':'REVALIDATED_NOT_PUBLISHABLE'},policy['category_mapping'],{'figure':image},policy)
        template='''<!doctype html><button type="button" onclick="document.querySelector('[role=dialog]').hidden=false">Create Blog</button>
        <div role="dialog" hidden><button role="combobox">Category</button><button role="combobox">Status</button>
        <button role="option">Safety</button><button role="option">Published</button><input id="externalSourceUrl">
        <button type="button" onclick="kind='cover';file.click()">Upload Cover Image</button>
        <button type="button" onclick="kind='gallery';file.click()">Upload Images</button><input id="file" type="file" style="display:none">
        <button role="tab" onclick="switchLang('vi')">Vietnamese</button><button role="tab" onclick="switchLang('en')">English</button>
        <input placeholder="Enter title in Vietnamese (5-200 characters)"><input placeholder="Enter title in English (5-200 characters)">
        <input placeholder="Enter summary in Vietnamese (max 500 characters)"><input placeholder="Enter summary in English (max 500 characters)">
        <input placeholder="SEO title (50-70 characters recommended)"><input placeholder="SEO description (120-160 characters recommended)">
        <div id="editor" class="ql-editor" contenteditable="true"></div><div><input placeholder="Add hashtag (2-50 characters each)"><button type="button" onclick="this.previousElementSibling.value=''">Add</button></div>
        <input type="checkbox"><input type="checkbox"><input id="publishedAt"><button type="button" onclick="sendCreate()">Create</button></div>
        <script>let kind='',cover=null,images=[],active='',locales={};const payload=PAYLOAD;const mutation=MUTATION;let file=document.getElementById('file'),editor=document.getElementById('editor');
        function saveLang(){if(!active)return;let label=active==='vi'?'Vietnamese':'English';let input=x=>document.querySelector('[placeholder="'+x+'"]');locales[active]={languageCode:active==='vi'?'VI':'EN',title:input('Enter title in '+label+' (5-200 characters)').value,summary:input('Enter summary in '+label+' (max 500 characters)').value,content:editor.innerHTML,metaTitle:input('SEO title (50-70 characters recommended)').value,metaDescription:input('SEO description (120-160 characters recommended)').value};}
        function switchLang(lang){saveLang();active=lang;editor.innerHTML='';document.querySelector('[placeholder="SEO title (50-70 characters recommended)"]').value='';document.querySelector('[placeholder="SEO description (120-160 characters recommended)"]').value='';}
        file.onchange=async()=>{let d=new FormData();d.append('file',file.files[0]);let r=await fetch('https://api.nailhub.ai/api/v1/Images/quick-upload',{method:'POST',body:d});let x=await r.json();x.id=kind==='cover'?'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa':'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';if(kind==='cover')cover=x;else images.push(x)};
        function sendCreate(){saveLang();let body={categoryId:'12345678-1234-1234-1234-123456789abc',status:payload.status,publishedAt:'2026-11-01T01:00:00.000Z',isPublic:payload.is_public,isFeatured:payload.is_featured,coverImage:cover,images,videoUrls:payload.video_urls,hashtags:payload.hashtags,externalSourceUrl:document.getElementById('externalSourceUrl').value,translations:[locales.vi,locales.en]};
        if(mutation==='title')body.translations[0].title='changed';if(mutation==='link')body.translations[0].content=body.translations[0].content.replace('www.cdc.gov','evil.example');if(mutation==='category')body.categoryId='00000000-0000-4000-8000-000000000000';if(mutation==='photo')body.images[0].imageUrl='https://evil.example/photo';
        fetch('https://api.nailhub.ai/api/v1/admin/blogs',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)}).catch(()=>{});}
        </script>'''.replace('PAYLOAD',json.dumps(data,ensure_ascii=False)).replace('MUTATION',json.dumps(mutation))
        seen=[];receipts=[];aborted=[]
        class ContextWrapper:
            def __init__(self,ctx):self.ctx=ctx
            def __getattr__(self,name):return getattr(self.ctx,name)
            def route(self,pattern,guard):
                def local(route):
                    request=route.request;seen.append((request.method,request.url))
                    class Wrapped:
                        def __init__(self):self.request=request
                        def abort(self,*args):aborted.append((request.method,request.url));return route.abort(*args)
                        def continue_(self):
                            if request.method=='GET':return route.fulfill(status=200,content_type='text/html; charset=utf-8',body=template)
                            path=f'/image-{len(receipts)}.jpg';receipt={'imageUrl':'https://vlink-storages-prod.sgp1.cdn.digitaloceanspaces.com'+path,'imageRelativePath':path,'thumbnailUrl':'','thumbnailRelativePath':''};receipts.append(receipt)
                            body=json.dumps(receipt).encode();return route.fulfill(status=200,headers={'content-type':'application/json','content-length':str(len(body)),'access-control-allow-origin':'*'},body=body)
                    guard(Wrapped())
                self.ctx.route(pattern,local)
        with sync_playwright() as playwright:
            if not Path(playwright.chromium.executable_path).is_file():self.skipTest('Chromium unavailable')
            class Engine:
                def launch_persistent_context(self,*args,**kwargs):return ContextWrapper(playwright.chromium.launch_persistent_context(*args,**kwargs))
            class ContextManager:
                def __enter__(self):return type('P',(),{'chromium':Engine()})()
                def __exit__(self,*args):return False
            visual_calls=[]
            def visual(root,folder,record_sha,observer):
                proof=observer.observation;visual_calls.append(proof['role'])
                output_sha=hashlib.sha256(proof['binding']['response']['imageUrl'].encode()).hexdigest()
                if mutation=='visual' and len(visual_calls)>2:output_sha='0'*64
                return {'status':'VISUAL_QA_NOT_PUBLISHABLE','record_sha256':record_sha,'media_id':proof['media_id'],
                        'source_sha256':proof['binding']['source_sha256'],'output_sha256':output_sha,
                        'comparison_policy_sha256':'a'*64,'thumbnail':None,'publication_enabled':False}
            patches=[patch('playwright.sync_api.sync_playwright',return_value=ContextManager()),
                     patch('source_mirror.official_create_canary._live_inputs',return_value=frozen),
                     patch('source_mirror.official_upload_controller._live_inputs',return_value=frozen),
                     patch('source_mirror.official_create_observer._live_inputs',return_value=frozen),
                     patch('source_mirror.official_upload_controller._sync_directory_chain'),
                     patch('source_mirror.official_create_observer._sync_directory_chain'),
                     patch('source_mirror.official_create_canary._sync_directory_chain'),
                     patch('source_mirror.official_create_canary.inspect_visual',side_effect=visual)]
            if os.name=='nt':
                # This fixture mocks POSIX persistence/authority, not transport.
                patches.append(patch('source_mirror.official_create_canary.UploadResponseStream',
                                     side_effect=lambda context,page,evidence:UploadResponseStream(context,page)))
            for p in patches:p.start();self.addCleanup(p.stop)
            if mutation is None: result=run(base.root,folder,sha)
            else:
                expected='visual evidence changed' if mutation=='visual' else 'aborted create verification incomplete'
                with self.assertRaisesRegex(ValueError,expected):run(base.root,folder,sha)
        if mutation is None:self.assertEqual(result['status'],'CANARY_ONLY_NOT_PUBLISHABLE')
        self.assertEqual(len(receipts),2)
        self.assertEqual(sum(method=='POST' and '/api/v1/admin/blogs' in url for method,url in seen),1)
        self.assertEqual(sum(method=='POST' and '/api/v1/admin/blogs' in url for method,url in aborted),1)
        self.assertFalse(list((base.root/'tmp').glob('official-canary-*')))

    def test_uploads_and_create_are_intercepted_and_create_aborts(self):self._run_case()
    def test_title_mutation_is_aborted(self):self._run_case('title')
    def test_link_mutation_is_aborted(self):self._run_case('link')
    def test_category_mutation_is_aborted(self):self._run_case('category')
    def test_photo_mutation_is_aborted(self):self._run_case('photo')
    def test_changed_visual_evidence_after_abort_is_held(self):self._run_case('visual')
