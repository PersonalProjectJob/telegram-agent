"""Isolated, upload-only canary. Native Create is always aborted."""
from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import stat
import tempfile
from urllib.parse import urlsplit
from .lock import worker_lock
from .official_editor_canary import allowed_read
from .official_upload_controller import UploadObserver

PROFILE_LIMIT=512*1024*1024
FILE_LIMIT=100*1024*1024
SKIP_DIRS={'Cache','Code Cache','GPUCache','Service Worker','Crashpad'}


@contextmanager
def isolated_profile(root):
    """Copy a locked dedicated profile privately; reject links and huge files."""
    root=Path(root).resolve(); browser=root/'browser'; source=browser/'nailhub-profile'; tmp=root/'tmp'
    if any(p.is_symlink() for p in (browser,source,tmp)) or not source.is_dir(): raise ValueError('unsafe profile location')
    tmp.mkdir(exist_ok=True,mode=0o700)
    # A prior interrupted copy can contain session credentials. Never delete it
    # automatically or proceed until an operator has reconciled it under lock.
    if any(tmp.glob('official-canary-*')): raise ValueError('previous canary profile requires reconciliation')
    total=0
    for folder,dirs,files in os.walk(source,followlinks=False):
        dirs[:]=[name for name in dirs if name not in SKIP_DIRS]
        for name in dirs:
            if (Path(folder)/name).is_symlink(): raise ValueError('symlink in browser profile')
        for name in files:
            if name.startswith('Singleton'): continue
            path=Path(folder)/name; info=path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_size>FILE_LIMIT: raise ValueError('unsupported browser profile file')
            total+=info.st_size
            if total>PROFILE_LIMIT: raise ValueError('browser profile exceeds limit')
    with tempfile.TemporaryDirectory(prefix='official-canary-',dir=tmp) as temporary:
        directory=Path(temporary)
        if directory.parent.resolve()!=tmp.resolve() or directory.is_symlink(): raise ValueError('unsafe temporary profile')
        copied=directory/'profile'
        # Copy symlinks as links, then reject them before Chromium starts. This
        # prevents a link introduced during copying from being followed.
        shutil.copytree(source,copied,ignore=shutil.ignore_patterns(*SKIP_DIRS,'Singleton*'),symlinks=True)
        for folder,dirs,files in os.walk(copied,followlinks=False):
            if any((Path(folder)/name).is_symlink() for name in dirs+files): raise ValueError('symlink in copied profile')
        yield copied


def run(root,evidence_path,record_sha,media_id):
    """Upload one approved image, observe exact request/response, never Create."""
    root=Path(root).resolve()
    with worker_lock(root):
        observer=UploadObserver(root,evidence_path,record_sha)
        observer.arm(media_id)
        data=observer.armed[1]
        if data.startswith(b'\xff\xd8\xff'): name,mime='official.jpg','image/jpeg'
        elif data.startswith(b'\x89PNG\r\n\x1a\n'): name,mime='official.png','image/png'
        else: raise ValueError('unsupported canary media')
        from playwright.sync_api import sync_playwright
        with isolated_profile(root) as profile, sync_playwright() as playwright:
            context=playwright.chromium.launch_persistent_context(str(profile),headless=True,service_workers='block',
                       args=['--no-startup-window','--disable-background-networking'])
            try:
                if not hasattr(context,'route_web_socket'): raise ValueError('websocket blocking unavailable')
                context.route_web_socket('**/*',lambda ws:ws.close())
                def guard(route):
                    req=route.request; url=urlsplit(req.url)
                    if req.method=='POST' and url.scheme=='https' and url.hostname=='api.nailhub.ai' and url.path=='/api/v1/Images/quick-upload':
                        observer.route(route)
                    elif allowed_read(req.method,req.url): route.continue_()
                    else: route.abort('blockedbyclient')
                context.route('**/*',guard)
                if any(page.url not in ('','about:blank') for page in context.pages): raise ValueError('unexpected startup page')
                page=context.new_page(); response=page.goto('https://nailhub.ai/admin/blogs',wait_until='domcontentloaded',timeout=30000)
                if not response or response.status!=200 or page.url!='https://nailhub.ai/admin/blogs': raise ValueError('admin canary unavailable')
                page.get_by_role('button',name='Create Blog',exact=True).click(timeout=30000)
                dialog=page.get_by_role('dialog')
                with page.expect_file_chooser() as chooser: dialog.get_by_role('button',name='Upload Images',exact=True).click()
                with page.expect_response(lambda result:observer.pending is not None and result.request is observer.pending[0],timeout=30000) as received:
                    chooser.value.set_files({'name':name,'mimeType':mime,'buffer':data})
                return observer.response(received.value)
            finally:
                context.close()
