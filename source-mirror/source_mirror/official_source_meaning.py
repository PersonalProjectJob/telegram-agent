"""Compare the supported, source-bound content; not a semantic proof."""
import base64
import hashlib
from .localization import packed
from .official_draft import source_snapshot
from .official_link_contract import links

def fingerprint(root,source_id,source_sha):
    snapshot=source_snapshot(root,source_id,source_sha)
    metadata=snapshot['metadata']
    raw=base64.b64decode(snapshot['source_html_base64'],validate=True)
    stable={key:metadata[key] for key in ('version','source_id','source_url','category','geographic_scope','title')}
    value={'metadata':stable,'reference_text':snapshot['reference_text'],
           'ordered_links':links(raw,metadata['source_url']),'source_label':snapshot['source_label']}
    return hashlib.sha256(packed(value).encode('utf-8')).hexdigest()

def differs_from_held(root,source_id,source_sha,held):
    candidate=fingerprint(root,source_id,source_sha)
    return all(fingerprint(root,source_id,old)!=candidate for old in held)
