import unittest
import base64
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from source_mirror.publisher import SEMANTIC_INLINE, wait_loaded_cover, wait_loaded_images

class InlineSemanticTests(unittest.TestCase):
    def test_split_emphasis_preserves_ranges_and_rejects_style_or_link_loss(self):
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            try:
                page=browser.new_page()
                def read(markup):
                    return page.evaluate("value => { const root=new DOMParser().parseFromString(value,'text/html').body; return ("+SEMANTIC_INLINE+")(root); }",markup)
                expected='<p><strong>Yuliia used <a href="https://example.com/">Glitterbels</a> products:</strong></p>'
                equivalent='<p><strong>Yuliia used </strong><a href="https://example.com/"><strong>Glitterbels</strong></a><strong> products:</strong></p>'
                unstyled='<p><strong>Yuliia used </strong><a href="https://example.com/">Glitterbels</a><strong> products:</strong></p>'
                wrong_link=equivalent.replace('https://example.com/','https://other.example/')
                self.assertEqual(read(expected),read(equivalent))
                self.assertNotEqual(read(expected),read(unstyled))
                self.assertNotEqual(read(expected),read(wrong_link))
            finally: browser.close()

class GuestCoverTests(unittest.TestCase):
    def test_guest_cover_wait_uses_guest_context(self):
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            try:
                admin_context=browser.new_context(); admin_context.new_page()
                guest_context=browser.new_context(); guest=guest_context.new_page()
                guest.set_content('<img src="data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 width=%221%22 height=%221%22%3E%3C/svg%3E">')
                wait_loaded_cover(guest,guest.locator('img'),timeout=5000)
            finally: browser.close()

    def test_admin_locale_images_wait_for_delayed_response_and_fail_on_broken_image(self):
        png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_GET(self):
                if self.path=='/slow.png':
                    time.sleep(.4)
                    try:
                        self.send_response(200);self.send_header('Content-Type','image/png');self.end_headers();self.wfile.write(png)
                    except OSError: pass  # Browser may cancel the pending request during navigation.
                elif self.path=='/broken.png': self.send_error(404)
                else:
                    body=b'<div class="prose"><img src="/slow.png"></div>'
                    self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(body)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True)
                try:
                    other=browser.new_context();other.new_page()
                    admin=browser.new_context();page=admin.new_page();url='http://127.0.0.1:'+str(server.server_port)
                    page.goto(url,wait_until='domcontentloaded');wait_loaded_images(page,page.locator('.prose'),1,timeout=5000)
                    page.goto(url+'/broken-page',wait_until='domcontentloaded')
                    page.locator('img').evaluate("img => img.src='/broken.png'")
                    with self.assertRaises(PlaywrightTimeoutError):wait_loaded_images(page,page.locator('.prose'),1,timeout=500)
                finally:browser.close()
        finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__': unittest.main()
