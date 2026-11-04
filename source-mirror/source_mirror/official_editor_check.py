"""Read-only editor-content check; does not authorize Create or reserve."""
import hashlib
from html.parser import HTMLParser
from .localization import strict_json
from .publisher import content_fingerprint


class AnchorAudit(HTMLParser):
    def __init__(self,markup):
        super().__init__(convert_charrefs=True); self.links=[]; self.feed(markup); self.close()
    def handle_starttag(self,tag,attrs):
        if tag!='a': return
        values=dict(attrs)
        if len(values)!=len(attrs) or set(values)-{'href','target','rel'} or not values.get('href'):
            raise ValueError('invalid editor anchor')
        self.links.append((values.get('href'),values.get('target'),values.get('rel')))


def check(payload_bytes,boundary,editor_contents):
    """Check both exact payload locales against observed editor semantics.

    Boundary status alone is not authentication. Caller must still run the
    trusted live boundary immediately before any future native operation.
    """
    if (not isinstance(payload_bytes,bytes) or not isinstance(boundary,dict)
            or boundary.get('status')!='REVALIDATED_NOT_PUBLISHABLE'
            or boundary.get('payload_sha256')!=hashlib.sha256(payload_bytes).hexdigest()):
        raise ValueError('editor boundary mismatch')
    payload=strict_json(payload_bytes.decode('utf-8'))
    if not isinstance(editor_contents,dict) or set(editor_contents)!={'vi','en'}: raise ValueError('both editor locales required')
    for language in ('vi','en'):
        try: expected=payload['locales'][language]['body_html']; actual=editor_contents[language]
        except (KeyError,TypeError): raise ValueError('editor payload missing') from None
        if not isinstance(expected,str) or not isinstance(actual,str): raise ValueError('editor content missing')
        if content_fingerprint(expected)!=content_fingerprint(actual) or AnchorAudit(expected).links!=AnchorAudit(actual).links:
            raise ValueError('editor content or source links changed')
    return {'status':'EDITOR_VERIFIED_NOT_PUBLISHABLE','payload_sha256':hashlib.sha256(payload_bytes).hexdigest()}
