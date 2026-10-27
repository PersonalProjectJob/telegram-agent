"""Image pipeline entry points for the Agy subscription route: deterministic helpers, gates and delegation.

Generation lives in `subscription_pipeline`; no API provider, paid ledger or model review is reachable from here."""
import os
import re
import json
import time
import unicodedata
from pathlib import Path

from ..admin_images import check_test_image_scope
from ..state import State
from ..usage import write_atomic as _write_atomic
from ..writer import slot_of
from . import ImageError
from .post import thumbnail


def safe_directory(value):
    path = Path(value).absolute()
    if any(parent.is_symlink() for parent in (path,*path.parents)):
        raise ImageError('output_symlink_forbidden')
    path.mkdir(parents=True,exist_ok=True,mode=0o700)
    if any(child.is_symlink() for child in path.iterdir()):
        raise ImageError('output_symlink_forbidden')
    if os.name == 'posix' and (path.stat().st_mode & 0o077 or path.stat().st_uid != os.getuid()):
        raise ImageError('private_output_required')
    return path


def write_atomic(path, value):
    path = Path(path)
    if path.is_symlink() or path.with_suffix('.tmp').is_symlink():
        raise ImageError('output_symlink_forbidden')
    _write_atomic(path,value)


def write_bytes(path, data):
    if path.is_symlink() or path.with_suffix('.part').is_symlink():
        raise ImageError('output_symlink_forbidden')
    temp = path.with_suffix('.part')
    with temp.open('wb') as stream:
        os.chmod(temp,0o600);stream.write(data);stream.flush();os.fsync(stream.fileno())
    os.replace(temp,path)


def load_json(path):
    if path.is_symlink() or path.stat().st_size > 4*1024*1024:
        raise ImageError('invalid_local_artifact')
    return json.loads(path.read_text(encoding='utf-8'))


def artifact(directory, name):
    if not isinstance(name,str) or not re.fullmatch(r'[a-z0-9-]+\.webp',name):
        raise ImageError('invalid_artifact_path')
    target = directory/name
    if target.is_symlink(): raise ImageError('output_symlink_forbidden')
    return target


def slots(article):
    title = article.get('title')
    if not isinstance(title,str) or not 5 <= len(title) <= 200:
        raise ImageError('article_title_required')
    result = []
    for section in article.get('sections',[]):
        slot = slot_of(section)
        if slot is None: continue
        if not isinstance(slot.get('brief'),str) or not slot['brief'].strip() or len(slot['brief']) > 2000:
            raise ImageError('image_brief_required')
        alt = slot.get('alt','')
        if not isinstance(alt,str) or not 5 <= len(alt.split()) <= 15:
            raise ImageError('image_alt_requires_5_to_15_words')
        subject=slot.get('subject','nail_editorial')
        if subject not in ('nail_editorial','nail_swatch_sticks'):raise ImageError('unsupported_image_subject')
        result.append({'brief':slot['brief'],'subject':subject,'alt':alt,'caption':str(slot.get('caption',''))[:500]})
    if not 3 <= len(result) <= 5:
        raise ImageError('three_to_five_image_slots_required')
    cover_subject=article.get('cover_image_subject','nail_editorial')
    if cover_subject not in ('nail_editorial','nail_swatch_sticks'):raise ImageError('unsupported_image_subject')
    cover_brief=article.get('cover_image_brief',title)
    if not isinstance(cover_brief,str) or not 1 <= len(cover_brief.strip()) <= 2000:
        raise ImageError('image_brief_required')
    return [{'brief':cover_brief,'subject':cover_subject,'alt':'Ảnh bìa minh họa '+' '.join(title.split()[:11]),'caption':''},*result]


BYPASS_STATUS='complete_with_review_bypass'
BYPASS_FLAG='image.review_bypass'


def bypass_block(names,hashes_):
    """Manifest audit of a TEMPORARY Owner-approved model-review bypass (see nailblog/AGENTS.md)."""
    return {'enabled':True,'positions':list(names),'hashes':list(hashes_)}


def check_bypass_manifest(manifest,names,hashes_):
    """Fail closed unless the manifest audit, positions and candidates all describe exactly this bypass."""
    audit=manifest.get('review_bypass')
    if (manifest.get('status')!=BYPASS_STATUS or audit!=bypass_block(names,hashes_) or
            manifest.get('automatic_flow_verdict')!='REVIEW_BYPASSED'):
        raise ImageError('review_bypass_audit_invalid')
    for name,sha in zip(names,hashes_):
        entry=manifest.get('positions',{}).get(name)
        if (not isinstance(entry,dict) or entry.get('status')!='review_bypassed' or entry.get('sha')!=sha or
                entry.get('scores') is not None or entry.get('review_intent') is not None or
                not (entry.get('intent') if manifest.get('provider') != 'subscription' else entry.get('subscription_job')) or
                not isinstance(entry.get('review_bypass'),dict) or entry['review_bypass'].get('config_flag')!=BYPASS_FLAG):
            raise ImageError('review_bypass_audit_invalid')


def slug(value):
    value = unicodedata.normalize('NFKD',value.replace('đ','d').replace('Đ','D')).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+','-',value).strip('-')[:80] or 'nailhub-blog'


class DispatchPaused(ImageError):
    """Local dispatch guard refused BEFORE transport invocation; no request sent."""
    def __init__(self, message, *, dispatched=False):
        super().__init__(message)
        self.dispatched = dispatched


class ImagePipeline:
    def __init__(self, config, *, state=None, provider=None, clock=time.time, cover_renderer=thumbnail,
                 before_dispatch=None):
        self.config, self.clock = config, clock
        self.state = state or State(config.db_path)
        self.default_renderer = thumbnail
        if provider is None and config.image.provider == 'subscription':
            from .subscription import SubscriptionImages
            provider = SubscriptionImages(config, clock=clock)
        self.provider = provider
        self.cover_renderer = cover_renderer
        self.before_dispatch = before_dispatch

    def check_dispatch(self):
        try:check_test_image_scope(self.config)
        except Exception:raise DispatchPaused('staging_test_image_capability_unavailable') from None
        if self.before_dispatch is not None:
            try:
                self.before_dispatch()
            except Exception:
                raise DispatchPaused('workflow_paused_before_dispatch') from None

    def reject_selected(self, out, position, expected_sha, reason):
        """Record an explicit Owner veto of one selected image (subscription manifests only)."""
        from .subscription_pipeline import reject_selected
        return reject_selected(self, out, position, expected_sha, reason)

    def historical_hashes(self):
        with self.state.connection() as db:
            # Selected historical records are kept conservatively even when no
            # reliable capture time is available in the Phase 1 image schema.
            values = db.execute("SELECT dhash FROM images WHERE chosen=1 AND dhash!=''").fetchall()
        result = []
        for value in values:
            try: result.append(int(value[0],16))
            except ValueError: raise ImageError('historical_image_hash_invalid') from None
        with self.state.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS image_selections(sha TEXT PRIMARY KEY,dhash TEXT NOT NULL,at REAL NOT NULL)')
            result += [int(row[0],16) for row in db.execute('SELECT dhash FROM image_selections WHERE at>=?',
                                                          (self.clock()-90*86400,))]
        return result

    async def run(self, article, out, run_id, ceiling=None, *, paid_enabled=False,free_enabled=False,
                  revision_of=None,revision_parent=None,revision_parent_sha=None,subscription_enabled=False):
        if self.config.image.provider != 'subscription':
            raise ImageError('subscription_provider_required')
        if paid_enabled or free_enabled:
            raise ImageError('api_image_routes_removed')
        from .subscription_pipeline import run
        if revision_of: raise ImageError('cli_revision_requires_owner_reconciliation')
        return await run(self, article, out, run_id, enabled=subscription_enabled,
                         paid_enabled=paid_enabled, free_enabled=free_enabled)
