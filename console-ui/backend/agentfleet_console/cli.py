"""Command-line entry point for the loopback Console."""
import argparse
import os
from pathlib import Path
import threading
import webbrowser

from .paths import DEFAULT_STATE_DIR
from .server import ConsoleServer
from .store import Store, APIError

def main():
    parser=argparse.ArgumentParser(description='AgentFleet local console')
    parser.add_argument('--port',type=int,default=8765);parser.add_argument('--no-open',action='store_true')
    parser.add_argument('--state-dir',type=Path,default=DEFAULT_STATE_DIR)
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
