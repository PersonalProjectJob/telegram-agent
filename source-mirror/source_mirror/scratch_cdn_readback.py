"""Bounded CDN readback and conservative decoded-image comparison."""
import http.client, io, ipaddress, queue, socket, ssl, threading, time
from urllib.parse import urlsplit
from PIL import Image, ImageChops, ImageOps, ImageStat
CDN = 'vlink-storages-prod.sgp1.cdn.digitaloceanspaces.com'
MAX_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 30_000_000
TIMEOUT = 12.0

def _readback(url, active):
    u = urlsplit(url)
    if (u.scheme != 'https' or u.hostname != CDN or u.username or u.password or u.port not in (None,443)
        or u.query or u.fragment or not u.path.startswith('/') or '%' in u.path or chr(92) in u.path
        or any(x in ('.','..') for x in u.path.split('/'))):
        raise ValueError('invalid CDN URL')
    deadline = time.monotonic() + TIMEOUT
    q = queue.Queue(maxsize=1)
    def resolve():
        try: q.put((True, socket.getaddrinfo(CDN,443,type=socket.SOCK_STREAM)))
        except Exception as e: q.put((False,e))
    threading.Thread(target=resolve,daemon=True).start()
    try: ok, rows = q.get(timeout=TIMEOUT)
    except queue.Empty: raise TimeoutError('CDN DNS deadline') from None
    if not ok or not rows: raise ValueError('CDN DNS failed')
    if any(not ipaddress.ip_address(row[4][0]).is_global for row in rows): raise ValueError('non-public CDN address')
    target=(rows[0][4][0],443)
    def remaining():
        n=deadline-time.monotonic()
        if n<=0: raise TimeoutError('CDN deadline')
        return n
    class PinnedHTTPS(http.client.HTTPSConnection):
        def connect(self):
            raw=socket.create_connection(target,timeout=remaining())
            active[0]=raw
            try:
                raw.settimeout(remaining())
                self.sock=ssl.create_default_context().wrap_socket(raw,server_hostname=CDN)
                self.checked_socket=self.sock
                active[0]=self.sock
            except BaseException:
                raw.close(); raise
    conn=PinnedHTTPS(CDN,timeout=remaining())
    try:
        conn.request('GET',u.path,headers={'Accept':'image/*','Connection':'close'})
        conn.checked_socket.settimeout(remaining())
        resp=conn.getresponse()
        if resp.status!=200: raise ValueError('unexpected CDN response')
        # Availability only: a successful HTTP 200 is the requested criterion.
        return 200
    finally: conn.close()

def readback(url):
    # The caller has an absolute deadline even if DNS, TLS, or an HTTP peer
    # trickles bytes. On expiry shut down any in-flight connection and fail shut.
    result=queue.Queue(maxsize=1); active=[None]
    def work():
        try: result.put((True,_readback(url,active)))
        except Exception as exc: result.put((False,exc))
    threading.Thread(target=work,daemon=True).start()
    try: ok,value=result.get(timeout=TIMEOUT)
    except queue.Empty:
        sock=active[0]
        if sock is not None:
            try: sock.shutdown(socket.SHUT_RDWR)
            except OSError: pass
            try: sock.close()
            except OSError: pass
        raise TimeoutError('CDN overall deadline') from None
    if not ok: raise value
    return value

def _image(data):
    if not isinstance(data,bytes) or not 0<len(data)<=MAX_BYTES: raise ValueError('invalid image bytes')
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.format not in ('JPEG','PNG','WEBP') or im.width*im.height>MAX_PIXELS or min(im.size)<1:
                raise ValueError('invalid image properties')
            im.load()
            return ImageOps.exif_transpose(im).convert('RGBA')
    except Exception as exc:
        raise ValueError('invalid image content') from exc

def compare(original,output,*,thumbnail=False):
    a,b=_image(original),_image(output)
    if a.size!=b.size:
        if not thumbnail or b.width>a.width or b.height>a.height or abs(a.width*b.height-a.height*b.width)>max(a.size):
            raise ValueError('image dimensions differ')
        a=a.resize(b.size,Image.Resampling.LANCZOS)
    # JPEG encoding may change pixels. Both mean error and RMS must stay low;
    # this is a conservative similarity guard, not proof of semantic identity.
    stat=ImageStat.Stat(ImageChops.difference(a,b))
    if max(stat.mean)>3.0 or max(stat.rms)>8.0: raise ValueError('CDN image content differs')
