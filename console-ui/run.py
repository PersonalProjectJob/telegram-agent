"""Source-bundled AgentFleet console, loopback only. Run as an unprivileged user."""
import argparse
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import threading
import webbrowser
from backend import Store, APIError

class ConsoleServer(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,address,store):
        self.store=store;self.csrf=secrets.token_urlsafe(32)
        super().__init__(address,functools.partial(PreviewHandler,directory=str(Path(__file__).resolve().parent)))

class PreviewHandler(SimpleHTTPRequestHandler):
    allowed={'/','/index.html','/tokens.css','/styles.css','/app.js','/api.js'}
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
            match=re.fullmatch(r'/api/services/(triage|nailhub)/(test|save|apply)',self.path)
            if not match:raise APIError('not_found','Resource not found.',404)
            id,action=match.groups()
            allowed={'expectedRevision'} if action=='apply' else {'settings','apiKey','expectedRevision'}
            if set(body)-allowed:raise APIError('invalid_body','Unknown request fields.')
            self.json(getattr(self.server.store,action)(id,body))
        except APIError as e:self.error(e)
        except Exception:self.error(APIError('server_error','Request could not be completed.',500))
    def end_headers(self):
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy',"default-src 'self'; style-src 'self' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; script-src 'self'; connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        super().end_headers()
    def log_message(self,*args):pass

def main():
    parser=argparse.ArgumentParser(description='AgentFleet local console')
    parser.add_argument('--port',type=int,default=8765);parser.add_argument('--no-open',action='store_true')
    parser.add_argument('--state-dir',type=Path,default=Path(__file__).resolve().parents[1]/'.agentfleet')
    parser.add_argument('--host-profile',choices=('local','installed'),default='local')
    args=parser.parse_args()
    if not 0<=args.port<=65535:parser.error('port must be between 0 and 65535')
    if hasattr(os,'geteuid') and os.geteuid()==0:parser.error('Run the web console as an unprivileged user.')
    os.umask(0o077)
    try:server=ConsoleServer(('127.0.0.1',args.port),Store(args.state_dir,args.host_profile=='installed'))
    except APIError as error:parser.exit(2,error.message+'\n')
    except OSError:parser.exit(2,'Cannot initialize console files or loopback port.\n')
    url=f'http://127.0.0.1:{server.server_port}';print(f'AgentFleet console: {url}',flush=True)
    print(f'Configuration binding: {args.host_profile}. Press Ctrl+C to stop.',flush=True)
    if not args.no_open:threading.Timer(.3,lambda:webbrowser.open(url)).start()
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()

if __name__=='__main__':main()
