import http.server
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from playwright.sync_api import sync_playwright
from source_mirror.official_direct_canary import block_create


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200);self.end_headers();self.wfile.write(b'<html></html>')
    def do_OPTIONS(self):
        self.server.options+=1;self.send_response(204);self.end_headers()
    def do_POST(self):
        self.server.posts+=1;self.send_response(200);self.end_headers()
    def log_message(self,*args):pass


class DirectCanaryTests(unittest.TestCase):
    def test_upload_and_options_passthrough_without_header_mutation(self):
        for method,url in [('POST','https://api.nailhub.ai/api/v1/Images/quick-upload'),('OPTIONS','https://api.nailhub.ai/api/v1/Images/quick-upload')]:
            route=SimpleNamespace(request=SimpleNamespace(method=method,url=url),continue_=Mock(),abort=Mock())
            block_create(route);route.continue_.assert_called_once_with();route.abort.assert_not_called()
        route=SimpleNamespace(request=SimpleNamespace(method='POST',url='https://api.nailhub.ai/api/v1/admin/blogs'),continue_=Mock(),abort=Mock())
        block_create(route);route.abort.assert_called_once();route.continue_.assert_not_called()

    def test_real_browser_blocks_post_and_passes_options(self):
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler);server.posts=0;server.options=0
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True)
                try:
                    context=browser.new_context();context.route('**/*',block_create);page=context.new_page();page.goto(f'http://127.0.0.1:{server.server_address[1]}/')
                    self.assertEqual(page.evaluate("async () => (await fetch('/probe',{method:'OPTIONS'})).status"),204)
                    result=page.evaluate("async () => {try{await fetch('/api/v1/admin/blogs',{method:'POST'});return 'sent'}catch(e){return 'blocked'}}")
                    self.assertEqual(result,'blocked');self.assertEqual(server.posts,0);self.assertEqual(server.options,1)
                    context.close()
                finally:browser.close()
        finally:server.shutdown();server.server_close();thread.join(5)
