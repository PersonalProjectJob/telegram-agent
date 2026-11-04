import http.server
import threading
import unittest
import tempfile
import os
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from playwright.sync_api import sync_playwright
from source_mirror.official_direct_request_canary import capture_or_block,persist_aborted_request

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200);self.end_headers();self.wfile.write(b'<html></html>')
    def do_POST(self):
        self.server.posts+=1;self.send_response(200);self.end_headers()
    def log_message(self,*args): pass

class RequestCanaryTest(unittest.TestCase):
    def test_private_request_capture_and_unsafe_existing_directory(self):
        with tempfile.TemporaryDirectory() as temp, patch('source_mirror.official_direct_request_canary._sync_directory_chain') as sync:
            folder=Path(temp);sha=persist_aborted_request(folder,folder,b'private')
            saved=folder/'aborted-create-requests'/(sha+'.json')
            self.assertEqual(saved.read_bytes(),b'private');sync.assert_called_once()
            if os.name=='posix':
                self.assertEqual(saved.stat().st_mode & 0o777,0o600)
                saved.parent.chmod(0o755)
                with self.assertRaisesRegex(ValueError,'unsafe request'):persist_aborted_request(folder,folder,b'another')
    def test_chromium_aborts_create_before_server_receives_it(self):
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler);server.posts=0
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();captured=[]
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True)
                try:
                    context=browser.new_context();page=context.new_page()
                    def guard(route):
                        req=route.request
                        if req.method=='POST':
                            proxy=SimpleNamespace(request=SimpleNamespace(url='https://api.nailhub.ai/api/v1/admin/blogs',method=req.method,post_data_buffer=req.post_data_buffer),abort=route.abort,continue_=route.continue_)
                            capture_or_block(proxy,captured)
                        else:route.continue_()
                    context.route('**/*',guard);page.goto(f'http://127.0.0.1:{server.server_address[1]}/')
                    result=page.evaluate("async () => {try {await fetch('/api/v1/admin/blogs',{method:'POST',body:'fixture'});return 'sent'} catch(e) {return 'blocked'}}")
                    self.assertEqual(result,'blocked');self.assertEqual(captured,[b'fixture']);self.assertEqual(server.posts,0)
                    context.close()
                finally:browser.close()
        finally:server.shutdown();server.server_close();thread.join(5)
