import copy
import json
import unittest
import tempfile
import os
import http.server
import threading
from types import SimpleNamespace
from unittest.mock import Mock,patch
import test_official_review_contract as fixture
from source_mirror.official_native_mock import mock_request
from source_mirror.official_scratch_publish import validate_post,publish,require_owned_worker_lock,canonical_html,create_route_guard
from source_mirror.lock import worker_lock


class DirectPostTests(unittest.TestCase):
    def test_html_serializer_only_attribute_order_and_entity_normalization(self):
        original='<p><a href="https://cdc.gov/a" target="_blank">It&#x27;s safe</a><img src="https://cdn/a"></p>'
        equivalent='<p><a target="_blank" href="https://cdc.gov/a">It\'s safe</a><img src="https://cdn/a"></p>'
        self.assertEqual(canonical_html(original),canonical_html(equivalent))
        for changed in (equivalent.replace('href="https://cdc.gov/a"','href="https://evil.example"'),equivalent.replace('<img','<img onerror="bad"'),equivalent.replace('<img src="https://cdn/a">',''),equivalent.replace('safe','unsafe'),equivalent.replace('</p>','<script>x</script></p>')):
            self.assertNotEqual(canonical_html(original),canonical_html(changed))

    def setUp(self):
        self.f=fixture.ContractTests();self.f.setUp()
        url='https://vlink-storages-prod.sgp1.cdn.digitaloceanspaces.com/example.png'
        self.image={'id':'image-id','imageUrl':url,'imageRelativePath':'/example.png','thumbnailUrl':url,'thumbnailRelativePath':'/example.png'}
        self.cover=dict(self.image,id='cover-id');self.mapping={'Safety':'12345678-1234-1234-1234-123456789abc'}
        self.native=mock_request(self.f.payload,self.cover,[self.image],self.mapping)
        self.receipts=[{'binding':{'response':{k:v for k,v in item.items() if k!='id'}}} for item in (self.cover,self.image)]

    def test_native_body_is_checked_after_post_against_canonical_and_receipts(self):
        raw=json.dumps(self.native).encode()
        self.assertEqual(validate_post(self.f.payload,self.mapping,self.receipts,raw),self.native)
        for field,value in (('externalSourceUrl','https://example.com/'),('isPublic',1),('categoryId','bad'),('hashtags',['other'])):
            changed=copy.deepcopy(self.native);changed[field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):validate_post(self.f.payload,self.mapping,self.receipts,json.dumps(changed).encode())
        changed=copy.deepcopy(self.native);changed['images'][0]['imageUrl']='https://example.com/x'
        with self.assertRaises(ValueError):validate_post(self.f.payload,self.mapping,self.receipts,json.dumps(changed).encode())

    def test_create_guard_blocks_changed_body_and_duplicate_and_passes_options(self):
        state={};guard=create_route_guard(self.f.payload,self.mapping,self.receipts,state)
        options=Mock();options.request.method='OPTIONS';guard(options);options.continue_.assert_called_once()
        valid=Mock();valid.request.method='POST';valid.request.url='https://api.nailhub.ai/api/v1/admin/blogs';valid.request.post_data_buffer=json.dumps(self.native).encode()
        guard(valid);valid.continue_.assert_called_once();self.assertTrue(state['validated'])
        duplicate=Mock();duplicate.request.method='POST';duplicate.request.url=valid.request.url;guard(duplicate);duplicate.abort.assert_called_once()
        changed=copy.deepcopy(self.native);changed['isPublic']=False
        invalid=Mock();invalid.request.method='POST';invalid.request.url=valid.request.url;invalid.request.post_data_buffer=json.dumps(changed).encode()
        guard2=create_route_guard(self.f.payload,self.mapping,self.receipts,{})
        guard2(invalid);invalid.abort.assert_called_once();invalid.continue_.assert_not_called()

    def test_create_guard_in_real_chromium_blocks_mismatch_before_server(self):
        from playwright.sync_api import sync_playwright
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):self.send_response(200);self.end_headers();self.wfile.write(b'<html></html>')
            def do_POST(self):self.server.posts+=1;self.send_response(200);self.end_headers()
            def log_message(self,*args):pass
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler);server.posts=0
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True)
                try:
                    for valid in (True,False):
                        state={};guard=create_route_guard(self.f.payload,self.mapping,self.receipts,state)
                        context=browser.new_context();page=context.new_page()
                        def route_local(route):
                            req=route.request
                            if req.method!='POST':return route.continue_()
                            proxy=SimpleNamespace(request=SimpleNamespace(url='https://api.nailhub.ai/api/v1/admin/blogs',method=req.method,post_data_buffer=req.post_data_buffer),abort=route.abort,continue_=route.continue_)
                            guard(proxy)
                        context.route('**/*',route_local);page.goto(f'http://127.0.0.1:{server.server_address[1]}/')
                        body=copy.deepcopy(self.native)
                        if not valid:body['isPublic']=not body['isPublic']
                        result=page.evaluate("async body => {try {return (await fetch('/create',{method:'POST',body})).status} catch(e) {return 'blocked'}}",json.dumps(body))
                        self.assertEqual(result,200 if valid else 'blocked');context.close()
                    self.assertEqual(server.posts,1)
                finally:browser.close()
        finally:server.shutdown();server.server_close();thread.join(5)

    def test_scheduler_lock_is_not_reacquired_before_boundary(self):
        row={'evidence_path':'/unused','record_sha':'a'*64,'state':'publishing','attempt_state':'intent',
             'reserved_day':'2026-10-30','slot_day':'2026-10-30','slot':'16:00'}
        with tempfile.TemporaryDirectory() as root:
            store=Mock();store.root=__import__('pathlib').Path(root);store.db.execute.return_value.fetchone.return_value=row
            with patch('source_mirror.official_publisher.available',return_value=True), \
                 patch('source_mirror.official_scratch_publish.require'), \
                 patch('source_mirror.official_scratch_publish.local_day',return_value='2026-10-30'), \
                 patch('source_mirror.official_scratch_publish.load_publish_policy',return_value={'enabled':True}), \
                 patch('source_mirror.official_scratch_publish.require_owned_worker_lock') if os.name=='nt' else patch('source_mirror.official_scratch_publish.nullcontext',wraps=__import__('contextlib').nullcontext), \
                 patch('source_mirror.official_scratch_publish._live_inputs',side_effect=RuntimeError('boundary reached')):
                with worker_lock(root),self.assertRaisesRegex(RuntimeError,'boundary reached'):
                    publish(store,{'id':'article'},1)

    @unittest.skipUnless(os.name=='posix','Linux flock ownership required')
    def test_unlocked_caller_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            with worker_lock(root): require_owned_worker_lock(root)
            with self.assertRaisesRegex(ValueError,'lock not owned'):require_owned_worker_lock(root)
