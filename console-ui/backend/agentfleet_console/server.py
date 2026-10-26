"""Loopback HTTP boundary and the existing Console API and public asset routes."""
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import secrets

from .paths import FRONTEND_DIR
from .store import Store, APIError

class ConsoleServer(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,address,store):
        self.store=store;self.csrf=secrets.token_urlsafe(32)
        super().__init__(address,functools.partial(PreviewHandler,directory=str(FRONTEND_DIR)))

class PreviewHandler(SimpleHTTPRequestHandler):
    assets={'/':'index.html','/index.html':'index.html','/tokens.css':'assets/tokens.css','/styles.css':'assets/styles.css','/app.js':'src/app.js','/api.js':'src/api.js','/state.js':'src/state.js','/schema-form.js':'src/schema-form.js'}
    allowed=set(assets)
    def translate_path(self,path):
        # Resolve only the legacy public URLs; source/package paths are never served.
        return str(FRONTEND_DIR/self.assets[path.split('?',1)[0]])
    def boundary(self,post=False):
        expected=f'127.0.0.1:{self.server.server_port}'
        if self.headers.get('Host')!=expected:raise APIError('forbidden_host','Use the printed loopback URL.',403)
        origin=self.headers.get('Origin')
        if origin is not None and origin!='http://'+expected:raise APIError('forbidden_origin','Same-origin requests only.',403)
        if self.headers.get('Sec-Fetch-Site') not in (None,'none','same-origin'):raise APIError('forbidden_origin','Same-origin requests only.',403)
        if post and not secrets.compare_digest(self.headers.get('X-CSRF-Token',''),self.server.csrf):raise APIError('csrf_failed','Reload the console before retrying.',403)
    def json(self,value,status=200):
        raw=json.dumps(value,ensure_ascii=False).encode();self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    def error(self,error):
        data={'code':error.code,'message':error.message}
        if error.field:data['field']=error.field
        self.json({'error':data},error.status)
    def do_GET(self):
        try:
            self.boundary()
            if self.path=='/api/bootstrap':
                result=self.server.store.bootstrap();result['csrfToken']=self.server.csrf;self.json(result);return
            if self.path=='/api/usage':self.json(self.server.store.usage());return
            match=re.fullmatch(r'/api/services/([a-z][a-z0-9_-]{0,31})/usage',self.path)
            if match:
                id=match.group(1);self.server.store.entry(id,'usage');self.json(self.server.store.usage(id));return
            if self.path.split('?',1)[0] not in self.allowed:raise APIError('not_found','Resource not found.',404)
            super().do_GET()
        except APIError as e:self.error(e)
        except Exception:self.error(APIError('server_error','Request could not be completed.',500))
    def do_HEAD(self):
        try:
            self.boundary()
            if self.path not in self.allowed:raise APIError('not_found','Resource not found.',404)
            super().do_HEAD()
        except APIError as e:self.error(e)
    def do_POST(self):
        try:
            self.boundary(True)
            if self.headers.get('Transfer-Encoding'):raise APIError('invalid_body','Chunked requests are not supported.')
            if self.headers.get('Content-Type','').split(';')[0]!='application/json':raise APIError('invalid_body','Expected JSON request.',415)
            try:size=int(self.headers.get('Content-Length','-1'))
            except ValueError:size=-1
            if not 0<=size<=65536:raise APIError('body_limit','Request exceeds size limit.',413)
            self.connection.settimeout(5)
            try:body=json.loads(self.rfile.read(size))
            except Exception:raise APIError('invalid_json','Invalid JSON request.') from None
            if not isinstance(body,dict):raise APIError('invalid_body','Expected a JSON object.')
            match=re.fullmatch(r'/api/services/([a-z][a-z0-9_-]{0,31})/(test|save|apply)',self.path)
            if not match:raise APIError('not_found','Resource not found.',404)
            id,action=match.groups()
            self.server.store.entry(id,'probe' if action=='test' else 'settings')
            allowed={'expectedRevision'} if action=='apply' else {'settings','apiKey','expectedRevision'}
            allowed.add('contractRevision')
            if set(body)-allowed:raise APIError('invalid_body','Unknown request fields.')
            self.json(getattr(self.server.store,action)(id,body))
        except APIError as e:self.error(e)
        except Exception:self.error(APIError('server_error','Request could not be completed.',500))
    def end_headers(self):
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy',"default-src 'self'; style-src 'self' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; script-src 'self'; connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        super().end_headers()
    def log_message(self,*args):pass
