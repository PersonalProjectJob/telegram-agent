import gzip
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch,MagicMock
from urllib.parse import urlsplit

from source_mirror.official_upload_response_stream import UploadResponseStream,LIMIT,MARKER


class ResponseStreamBrowserTests(unittest.TestCase):
    def run_case(self,mode):
        try:from playwright.sync_api import sync_playwright
        except ImportError:self.skipTest('Playwright unavailable')
        hits=[]
        class Handler(BaseHTTPRequestHandler):
            protocol_version='HTTP/1.1'
            def log_message(self,*args):pass
            def do_GET(self):
                body=b'''<button onclick="fetch('/upload',{method:'POST',body:'fixture'}).then(r=>r.text()).then(t=>document.querySelector('output').textContent='done').catch(e=>document.querySelector('output').textContent='error')">Upload</button><button onclick="fetch('/create',{method:'POST',body:'forbidden'}).catch(e=>document.querySelector('aside').textContent='blocked')">Danger</button><output></output><aside></aside>'''
                if mode.startswith('cors'):
                    endpoint=self.server.upload_origin+'/upload'
                    options="method:'PUT',headers:{Authorization:'fixture'},body:'fixture'" if mode=='cors-method' else "method:'POST',headers:{Authorization:'fixture','X-Unapproved':'x'},body:'fixture'" if mode=='cors-header' else "method:'POST',headers:{Authorization:'fixture'},body:'fixture'"
                    body=body.replace(b"'/upload',{method:'POST',body:'fixture'}",("'"+endpoint+"',{"+options+"}").encode())
                self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
            def do_OPTIONS(self):
                self.send_response(204);self.send_header('Access-Control-Allow-Origin',self.headers.get('Origin',''))
                self.send_header('Access-Control-Allow-Methods','POST, PUT');self.send_header('Access-Control-Allow-Headers',self.headers.get('Access-Control-Request-Headers',''))
                self.send_header('Content-Length','0');self.end_headers()
            def do_POST(self):
                hits.append(self.path);self.rfile.read(int(self.headers.get('Content-Length','0')))
                value={'imageUrl':'https://cdn.example/image.jpg','label':'Tiếng Việt'}
                if mode=='oversize':value={'large':'x'*(LIMIT+1)}
                body=json.dumps(value,ensure_ascii=False).encode()
                if mode=='gzip':body=gzip.compress(body)
                self.send_response(200);self.send_header('Content-Type','text/html' if mode=='wrongtype' else 'application/json')
                if mode.startswith('cors'):self.send_header('Access-Control-Allow-Origin',self.headers.get('Origin',''))
                self.send_header(MARKER,'server-spoof')
                if mode=='gzip':self.send_header('Content-Encoding','gzip')
                self.send_header('Transfer-Encoding','chunked');self.end_headers()
                try:
                    for start in range(0,len(body),8192):
                        chunk=body[start:start+8192];self.wfile.write(('%x\r\n'%len(chunk)).encode()+chunk+b'\r\n');self.wfile.flush()
                    self.wfile.write(b'0\r\n\r\n');self.wfile.flush()
                except (BrokenPipeError,ConnectionResetError):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        origin='http://127.0.0.1:'+str(server.server_port);routed=[];responses=[]
        endpoint=origin+'/upload';appserver=None;appworker=None
        if mode.startswith('cors'):
            appserver=ThreadingHTTPServer(('127.0.0.1',0),Handler);appserver.upload_origin=origin
            appworker=threading.Thread(target=appserver.serve_forever,daemon=True);appworker.start()
            origin='http://127.0.0.1:'+str(appserver.server_port)
        try:
            with sync_playwright() as pw:
                try:browser=pw.chromium.launch(headless=True)
                except Exception as exc:self.skipTest('Chromium unavailable: '+type(exc).__name__)
                try:
                    context=browser.new_context(service_workers='block');page=context.new_page()
                    def guard(route):
                        request=route.request;routed.append((request.method,urlsplit(request.url).path))
                        if request.method=='GET' or (request.method=='POST' and request.url==endpoint):route.continue_()
                        else:route.abort('blockedbyclient')
                    context.route('**/*',guard)
                    with patch('source_mirror.official_upload_response_stream.UPLOAD_ENDPOINT',endpoint),\
                         patch('source_mirror.official_upload_response_stream.EXPECTED_ORIGIN',origin if mode!='cors-origin' else 'https://wrong.example'):
                        stream=UploadResponseStream(context,page)
                        page.on('response',lambda response:responses.append(response) if response.url==endpoint else None)
                        page.goto(origin);page.get_by_role('button',name='Danger',exact=True).click()
                        page.locator('aside').get_by_text('blocked',exact=True).wait_for(timeout=10000)
                        self.assertNotIn('/create',hits);self.assertIn(('POST','/create'),routed)
                        page.get_by_role('button',name='Upload',exact=True).click()
                        if mode=='concurrent':page.get_by_role('button',name='Upload',exact=True).click()
                        expected='error' if mode in ('oversize','wrongtype','cors-origin','cors-header','cors-method') else 'done'
                        try:page.locator('output').get_by_text(expected,exact=True).wait_for(timeout=10000)
                        except Exception:
                            self.fail('response outcome='+page.locator('output').inner_text()+' failures='+str(stream.failures)+' records='+str(len(stream.records)))
                        if expected=='done':
                            if mode=='concurrent':
                                for _ in range(100):
                                    if len(responses)==2:break
                                    page.wait_for_timeout(20)
                            self.assertEqual(len(responses),2 if mode=='concurrent' else 1)
                            first=responses[0]
                            self.assertNotEqual(first.headers[MARKER],'server-spoof')
                            if mode=='concurrent':
                                spoof=SimpleNamespace(headers=responses[1].headers,url=first.url,request=first.request,status=first.status)
                                with self.assertRaises(ValueError):stream.read(spoof)
                                self.assertNotEqual(first.headers[MARKER],responses[1].headers[MARKER])
                            raw=stream.read(first)
                            self.assertEqual(first.headers.get('content-length'),str(len(raw)))
                            self.assertNotIn('content-encoding',first.headers);self.assertNotIn('transfer-encoding',first.headers)
                            self.assertLessEqual(len(raw),LIMIT);self.assertEqual(json.loads(raw)['label'],'Tiếng Việt')
                            with self.assertRaises(ValueError):stream.read(first)
                            if mode=='concurrent':self.assertEqual(json.loads(stream.read(responses[1]))['label'],'Tiếng Việt')
                            self.assertEqual(stream.failures,[])
                        else:self.assertTrue(stream.failures);self.assertEqual(stream.records,{})
                        if mode in ('cors-origin','cors-header','cors-method'):self.assertEqual(hits,[])
                        else:self.assertEqual(hits,['/upload']*(2 if mode=='concurrent' else 1));self.assertIn(('POST','/upload'),routed)
                        stream.close();context.close()
                finally:browser.close()
        finally:
            server.shutdown();server.server_close();worker.join(timeout=5)
            if appserver is not None:appserver.shutdown();appserver.server_close();appworker.join(timeout=5)

    def test_chunked_native_response_and_request_guard_coexist(self):self.run_case('valid')
    def test_gzip_native_response_is_decoded_bounded_and_replayed_once(self):self.run_case('gzip')
    def test_oversize_response_aborts_without_body_fallback(self):self.run_case('oversize')
    def test_wrong_content_type_aborts(self):self.run_case('wrongtype')
    def test_concurrent_marker_spoof_cannot_consume_other_native_response(self):self.run_case('concurrent')
    def test_real_cross_origin_preflight_preserves_post_and_create_guards(self):self.run_case('cors-valid')
    def test_cross_origin_wrong_origin_never_sends_post(self):self.run_case('cors-origin')
    def test_cross_origin_unapproved_requested_header_never_sends_post(self):self.run_case('cors-header')
    def test_cross_origin_requested_put_never_sends_post(self):self.run_case('cors-method')


if __name__=='__main__':unittest.main()


class ReceiptPersistenceTests(unittest.TestCase):
    def test_persist_fault_always_aborts_native_response(self):
        stream=object.__new__(UploadResponseStream);stream.records={};stream.failures=[];stream.session=MagicMock()
        stream._persist=MagicMock(side_effect=OSError('storage unavailable'))
        from source_mirror.official_upload_response_stream import UPLOAD_ENDPOINT
        stream._paused({'requestId':'native-id','networkId':'network-id','responseStatusCode':200,
                        'request':{'url':UPLOAD_ENDPOINT,'method':'POST'}})
        stream.session.send.assert_any_call('Fetch.failRequest',{'requestId':'native-id','errorReason':'BlockedByClient'})
        self.assertIn('EvidencePersistenceError',stream.failures)

    @unittest.skipUnless(os.name=='posix','POSIX private persistence required')
    def test_private_owner_mode_exclusive_and_no_follow(self):
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp)/'receipts';directory.mkdir(mode=0o700);context=MagicMock();page=MagicMock()
            stream=UploadResponseStream(context,page,directory)
            stream._persist('a'*32,{'stage':'headers'})
            path=directory/(('a'*32)+'.headers.json');original=path.read_bytes()
            with self.assertRaises(FileExistsError):stream._persist('a'*32,{'stage':'headers','changed':True})
            self.assertEqual(path.read_bytes(),original)
            victim=Path(temp)/'victim';victim.write_bytes(b'unchanged')
            link=directory/(('b'*32)+'.HOLD.receipt');link.symlink_to(victim)
            with self.assertRaises(FileExistsError):stream._persist('b'*32,{'stage':'HOLD'},b'changed')
            self.assertEqual(victim.read_bytes(),b'unchanged')
            directory.chmod(0o755);context.reset_mock()
            with self.assertRaisesRegex(ValueError,'private and owned'):UploadResponseStream(context,page,directory)
            context.new_cdp_session.assert_not_called()
