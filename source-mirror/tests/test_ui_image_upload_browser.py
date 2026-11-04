"""Local real-browser proof for the shared chooser, without a PROD upload."""
import hashlib
import http.server
import threading
import unittest
from playwright.sync_api import sync_playwright
from source_mirror.ui_image_upload import upload_with_chooser
from source_mirror.official_upload_evidence import verify_request


class Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        size=int(self.headers['Content-Length']);self.server.last_body=self.rfile.read(size)
        self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length','2');self.end_headers();self.wfile.write(b'{}')
    def log_message(self,*args): pass


class BrowserUploadTest(unittest.TestCase):
    def test_actual_file_chooser_posts_identical_multipart_bytes(self):
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            raw=b'\xff\xd8\xfftest-image'; port=server.server_address[1]
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True)
                try:
                    page=browser.new_page()
                    page.route('**/fixture',lambda route:route.fulfill(status=200,content_type='text/html',body='''<div role="dialog"><button onclick="document.getElementById('f').click()">Upload Images</button><input id="f" type="file" hidden onchange="const x=new FormData();x.append('file',this.files[0]);fetch('/upload',{method:'POST',body:x})"></div>'''))
                    page.goto(f'http://127.0.0.1:{port}/fixture')
                    result=upload_with_chooser(page,page.get_by_role('dialog'),'Upload Images',{'name':'x.jpg','mimeType':'image/jpeg','buffer':raw},lambda r:r.url.endswith('/upload') and r.ok)
                    verify_request(result.request.post_data_buffer,result.request.headers['content-type'],raw)
                    self.assertEqual(hashlib.sha256(server.last_body).digest(),hashlib.sha256(result.request.post_data_buffer).digest())
                finally: browser.close()
        finally: server.shutdown();server.server_close();thread.join(5)
