"""Explicit, resumable local image generation with durable paid intents and SHA-bound review."""
import asyncio
import html
import hashlib
import json
import os
import re
import time
import unicodedata
from dataclasses import asdict
from importlib.resources import files
from pathlib import Path

from .._runtime import ProcessLock
from ..admin_images import validate_files, verify_reviews, test_image_audit, test_image_entry, check_test_image_scope, test_image_cover_sha, test_image_original_archive, active_test_image_v2
from ..model_lock import MODEL_LOCK
from ..paid import IntentExists, PaidLedger, cancel_pending_before_dispatch
from ..publication import digest
from ..state import State
from ..usage import BudgetExceeded, UsageRecorder, write_atomic as _write_atomic
from ..writer import render_html, slot_of
from . import ImageError
from .post import VERSION, crop, hashes, thumbnail, webp, decode
from .provider import GeminiImages, image_of, parts_of, review_of, usage
from .prompts import VERSION as PROMPT_VERSION,REVIEW_VERSION,generation_prompt,feasible_brief,rejection_feedback


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


STALE_MARGIN_S=600  # pending older than image.timeout_seconds + this margin is presumed crashed


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
        if config.image.provider == 'subscription':
            from .subscription import SubscriptionImages
            self.ledger = None
            self.provider = provider or SubscriptionImages(config, clock=clock)
        elif config.image.provider=='cloudflare':
            from .cloudflare import CloudflareImages
            from .neurons import NeuronLedger
            self.ledger=NeuronLedger(config,clock)
            self.provider=provider or CloudflareImages(config)
        else:
            self.ledger = PaidLedger(self.state,config,clock)
            self.provider = provider or GeminiImages(config)
        self.recorder = UsageRecorder(self.state,config,clock=clock)
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

    def cancel_undispatched(self, intent):
        table = 'cf_intents' if self.config.image.provider == 'cloudflare' else 'paid_intents'
        if table=='cf_intents':return self.ledger.cancel_undispatched(intent)
        return cancel_pending_before_dispatch(self.ledger.state, table, intent, self.clock)

    def reject_selected(self,out,position,expected_sha,reason):
        """Record an operator veto without changing model scores, bytes or quota.

        An old completed preview is retained as a stale snapshot. The normal
        resume path must select a different, already approved or untried
        candidate and revalidate the complete document before publishing it.
        """
        if self.config.image.provider == 'subscription':
            from .subscription_pipeline import reject_selected
            return reject_selected(self, out, position, expected_sha, reason)
        if (not re.fullmatch(r'cover|body-[1-5]',str(position)) or
                not isinstance(expected_sha,str) or not re.fullmatch(r'[0-9a-f]{64}',expected_sha) or
                not isinstance(reason,str) or not 1<=len(reason.strip())<=1000 or not reason.isprintable()):
            raise ImageError('operator_rejection_arguments_invalid')
        directory=safe_directory(out)
        with ProcessLock(directory):
            manifest_path=directory/'manifest.json';manifest=load_json(manifest_path)
            run_id=manifest.get('run_id');binding=manifest.get('binding')
            table='cf_runs' if self.config.image.provider=='cloudflare' else 'paid_runs'
            with self.ledger.state.connection() as db:
                row=db.execute('SELECT binding FROM '+table+' WHERE id=?',(run_id,)).fetchone()
            if not row or row['binding']!=binding:
                raise ImageError('operator_rejection_run_binding_mismatch')
            operations=self.ledger.report(run_id)
            if (any(row['status'] in ('pending','uncertain') for row in operations) or
                    any(entry.get('status') in ('pending','uncertain')
                        for entry in manifest.get('candidates',{}).values())):
                raise ImageError('operator_rejection_requires_reconciliation')
            selected=manifest.get('positions',{}).get(position)
            if not selected or selected.get('sha')!=expected_sha:
                raise ImageError('operator_rejection_selected_sha_mismatch')
            target=artifact(directory,selected.get('file'))
            actual_sha,_=hashes(target.read_bytes())
            if actual_sha!=expected_sha:raise ImageError('operator_rejection_selected_bytes_changed')
            candidates=[(name,entry) for name,entry in manifest.get('candidates',{}).items()
                        if name.startswith(position+'-r') and entry.get('sha')==expected_sha and
                        entry.get('file')==selected['file'] and entry.get('status') in ('approved','review_bypassed')]
            if len(candidates)!=1:raise ImageError('operator_rejection_candidate_identity_ambiguous')
            name,candidate=candidates[0]
            event={'at':self.clock(),'position':position,'sha':expected_sha,'candidate':name,
                   'reason':reason.strip(),'model_status':candidate['status'],
                   'model_scores':candidate.get('scores'),'model_reason':candidate.get('reason'),
                   'previous_document_digest':manifest.get('document_digest'),'stale_artifacts':[]}
            sequence=len(manifest.get('operator_rejections',[]))+1
            for filename in ('document.json','preview.html'):
                previous=directory/filename
                if previous.exists():
                    if previous.is_symlink() or previous.stat().st_size>4*1024*1024:
                        raise ImageError('invalid_local_artifact')
                    data=previous.read_bytes();backup=previous.with_name(previous.stem+f'-stale-{sequence}-'+expected_sha[:12]+previous.suffix)
                    if backup.exists() and (backup.is_symlink() or backup.read_bytes()!=data):
                        raise ImageError('operator_rejection_backup_conflict')
                    if not backup.exists():write_bytes(backup,data)
                    event['stale_artifacts'].append(backup.name)
            candidate['status']='operator_rejected';candidate['operator_rejection']=event
            manifest['positions'].pop(position)
            manifest.setdefault('operator_rejections',[]).append(event)
            manifest.update(status='held',error='operator_rejected_selected_image',artifacts_stale=True)
            manifest.pop('document_digest',None)
            write_atomic(manifest_path,manifest)
            return event

    async def paid(self, run_id, intent, model, body, input_ceiling, output_ceiling, *, image=False,review_bound=0,pair_intent=None):
        self.check_dispatch()
        if self.config.image.provider=='cloudflare':
            return await self.free_call(run_id,intent,model,body,review_bound=review_bound,pair_intent=pair_intent)
        price_in = self.config.budget.text_prices.get(model)
        if price_in is None or price_in[0] <= 0 or price_in[1] <= 0: raise ImageError('positive_model_price_required')
        output_rate = self.config.budget.image_output_prices.get(model) if image else price_in[1]
        if not isinstance(output_rate,(int,float)) or output_rate <= 0 or output_rate < price_in[1]:
            raise ImageError('positive_output_price_required')
        bound = (input_ceiling*price_in[0] + output_ceiling*output_rate)/1e6
        unit_price = size = None
        if image:
            size = body.get('generationConfig',{}).get('imageConfig',{}).get('imageSize') if isinstance(body,dict) else None
            unit_price = self.config.budget.image_unit_prices.get(model,{}).get(size)
            if unit_price is not None: bound = max(bound,unit_price)
        if self.ledger.get(intent): raise ImageError('existing_intent_requires_reconciliation')
        self.recorder.check()  # at the cap: refuse before ANY transport call, countTokens included
        # countTokens is a bounded non-generation preflight, not a paid retry.
        def count():
            if self.before_dispatch is None:
                return self.provider.count(model,body,input_ceiling)
            with MODEL_LOCK.enter(background=True):
                self.check_dispatch()
                return self.provider.count(model,body,input_ceiling)
        await asyncio.to_thread(count)
        self.recorder.check()
        try:
            self.ledger.reserve(intent,'gemini',model,bound,run_id=run_id)
        except IntentExists:
            raise ImageError('existing_intent_requires_reconciliation') from None
        def call():
            with MODEL_LOCK.enter(background=True):
                self.check_dispatch()
                return self.provider.complete(model,body)
        task = asyncio.create_task(asyncio.to_thread(call))
        try:
            try:
                envelope = await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise
            parts = parts_of(envelope)
            tokens_in,tokens_out = usage(envelope)
            if tokens_in > input_ceiling or tokens_out > output_ceiling:
                raise ImageError('reported_usage_exceeds_reserved_bound')
            cost = (tokens_in*price_in[0]+tokens_out*output_rate)/1e6
            units = sum('inlineData' in part for part in parts) if image else tokens_in+tokens_out
            metadata = {'input':tokens_in,'output_including_thinking':tokens_out,
                        'cost_basis':'all_output_at_conservative_rate','output_per_m':output_rate}
            if unit_price is not None:
                metadata['unit_price_basis'] = {'token_cost_usd':cost,'unit_price_usd':unit_price,'size':size,
                                                'settled_usd':max(cost,unit_price)}
                cost = max(cost,unit_price)
            self.ledger.settle(intent,units,cost,metadata)
            self.recorder.publish()
            return envelope
        except DispatchPaused:
            self.cancel_undispatched(intent)
            raise
        except BaseException:
            self.ledger.uncertain(intent)
            raise

    async def free_call(self,run_id,intent,model,body,*,review_bound=0,pair_intent=None):
        self.check_dispatch()
        if self.ledger.get(intent):raise ImageError('existing_intent_requires_reconciliation')
        self.ledger.reserve(intent,'cloudflare',model,self.provider.forecast(model,body),run_id=run_id,review_bound=review_bound,pair_intent=pair_intent)
        def call():
            with MODEL_LOCK.enter(background=True):
                self.ledger.assert_dispatch_day(run_id)
                self.check_dispatch()
                return self.provider.complete(model,body)
        task=asyncio.create_task(asyncio.to_thread(call))
        try:
            try:envelope=await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise
            parts_of(envelope)
            charge,metadata=self.provider.observed(model,envelope,body)
            self.ledger.settle(intent,1,charge,metadata)
            return envelope
        except DispatchPaused:
            self.cancel_undispatched(intent)
            raise
        except BaseException:
            self.ledger.uncertain(intent)
            raise

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

    async def run(self, article, out, run_id, ceiling, *, paid_enabled=False,free_enabled=False,
                  revision_of=None,revision_parent=None,revision_parent_sha=None,subscription_enabled=False):
        if self.config.image.provider == 'subscription':
            from .subscription_pipeline import run
            if revision_of: raise ImageError('cli_revision_requires_owner_reconciliation')
            return await run(self, article, out, run_id, enabled=subscription_enabled,
                             paid_enabled=paid_enabled, free_enabled=free_enabled)
        if subscription_enabled: raise ImageError('subscription_provider_required')
        is_cloudflare=self.config.image.provider=='cloudflare'
        if is_cloudflare and (not free_enabled or paid_enabled):raise ImageError('explicit_cloudflare_free_enable_required')
        if not is_cloudflare and (not paid_enabled or free_enabled):raise ImageError('explicit_paid_enable_required')
        positions = slots(article)
        cfg = self.config.image
        if bool(revision_of)!=bool(revision_parent):raise ImageError('revision_parent_required')
        if revision_of and (not is_cloudflare or cfg.candidates*cfg.max_rounds>2):
            raise ImageError('revision_max_two_candidates_per_position')
        try:
            for slot in positions:feasible_brief(slot['brief'],slot['subject'],revision=bool(revision_of))
        except ValueError as error:
            raise ImageError(str(error)) from None
        parent=None
        if revision_of:
            parent_path=Path(revision_parent).absolute()
            if (not parent_path.is_file() or any(p.is_symlink() for p in (parent_path,*parent_path.parents))):
                raise ImageError('revision_parent_manifest_invalid')
            if (not re.fullmatch(r'[0-9a-f]{64}',str(revision_parent_sha or '')) or
                    parent_path.stat().st_size>4*1024*1024):
                raise ImageError('revision_parent_manifest_sha_required')
            parent_bytes=parent_path.read_bytes()
            if hashlib.sha256(parent_bytes).hexdigest()!=revision_parent_sha:
                raise ImageError('revision_parent_manifest_changed')
            parent=load_json(parent_path)
            if parent!=json.loads(parent_bytes):raise ImageError('revision_parent_manifest_changed')
            if (parent.get('run_id')!=revision_of or parent.get('status')!='held' or
                    parent.get('error') not in ('image_quality_held','operator_rejected_selected_image') or
                    not isinstance(parent.get('binding'),str) or not parent['binding'] or
                    not parent.get('candidates') or
                    any(e.get('status') not in ('approved','rejected','operator_rejected','review_bypassed')
                        for e in parent.get('candidates',{}).values())):
                raise ImageError('revision_parent_manifest_requires_reconciliation')
            for entry in parent['candidates'].values():
                for key in ('intent','review_intent'):
                    if key not in entry or (key=='review_intent' and entry[key] is None):
                        if key=='intent':raise ImageError('revision_parent_candidate_intent_missing')
                        continue
                    receipt=self.ledger.get(entry[key])
                    if (not receipt or receipt['run_id']!=revision_of or
                            receipt['status'] not in ('settled','estimated')):
                        raise ImageError('revision_parent_candidate_requires_reconciliation')
                if entry.get('file'):
                    source=artifact(parent_path.parent,entry['file'])
                    if source.stat().st_size>16*1024*1024 or hashlib.sha256(source.read_bytes()).hexdigest()!=entry.get('sha'):
                        raise ImageError('revision_parent_candidate_changed')
        if cfg.provider not in ('gemini','cloudflare') or not cfg.model_cover or not cfg.model_body or not cfg.review_model:
            raise ImageError('gemini_models_required')
        directory = safe_directory(out)
        with ProcessLock(directory):
            if not is_cloudflare:
                # Crash recovery before any new reservation: stale pending -> uncertain (still counted); uncertain
                # older than a day -> settled at the full reserved bound on its original day.
                now = self.clock()
                self.ledger.reconcile_stale(now,cfg.timeout_seconds+STALE_MARGIN_S,run_id=run_id)
                self.ledger.age_out_uncertain(now)
            params = asdict(cfg);params.pop('key_file')
            # Switch OFF keeps the historical binding digest; ON changes it (resume fails closed both ways).
            if not cfg.review_bypass:params.pop('review_bypass')
            # Same pattern for the autoflow fields: defaults keep the pre-autoflow digest so older runs still resume.
            if cfg.key_source=='file':params.pop('key_source')
            prices = asdict(self.config.budget)
            if not prices['image_unit_prices']:prices.pop('image_unit_prices')
            # Same for the shared-ledger path: unset keeps the pre-S10a digest; set stays IN the binding (a run must not
            # silently move between ledgers).
            if not prices.get('ledger_db_path'):prices.pop('ledger_db_path',None)
            binding = digest({'article':article,'image':params,'prices':prices,
                              'processing':VERSION,'review':REVIEW_VERSION,'directory':str(directory),
                              'template':digest(files('nailblog.images').joinpath('templates/cover.html').read_text(encoding='utf-8')),
                              'prompt_version':PROMPT_VERSION,
                              **({'revision':{'parent_run':revision_of,'manifest_sha':revision_parent_sha}}
                                 if parent else {}),
                              'cloudflare':({'config':asdict(self.config.cloudflare),'pricing':'cf-20261019-v1',
                                             'generation_dimensions':[[1600,960],[1280,720]],
                                             'neurons_per_tile':26.05,'review_rates':[40909,290909],
                                             'reference':self.provider.reference_binding(),'input_neurons_per_tile':5.37}
                                            if is_cloudflare else None)})
            manifest_path = directory/'manifest.json'
            waiver=test_image_audit(self.config,article,run_id,binding,directory)
            bypass=cfg.review_bypass
            if bypass and waiver is not None:raise ImageError('review_bypass_test_waiver_conflict')
            if not bypass and manifest_path.exists() and load_json(manifest_path).get('status')==BYPASS_STATUS:
                raise ImageError('review_bypass_disabled')
            original_archive=test_image_original_archive(self.config)
            if original_archive is not None:
                current=load_json(manifest_path)
                for entry in current.get('candidates',{}).values():
                    for key in ('intent','review_intent'):
                        liability=self.ledger.get(entry.get(key)) if entry.get(key) else None
                        if not liability or liability['run_id']!=run_id or liability['status'] not in ('settled','estimated'):
                            raise ImageError('test_image_candidate_requires_reconciliation')
                if current.get('test_image_waiver_archive') not in (None,original_archive):raise ImageError('test_image_archive_conflict')
                if 'test_image_waiver_archive' not in current:
                    current['test_image_waiver_archive']=original_archive;write_atomic(manifest_path,current)
            if is_cloudflare and manifest_path.exists() and load_json(manifest_path).get('status') in ('complete','complete_with_test_waiver',BYPASS_STATUS):
                self.ledger.verify_run(run_id,binding,ceiling)
            else:
                if parent:
                    self.ledger.run(run_id,binding,ceiling,revision_of=revision_of,parent_binding=parent['binding'])
                else:self.ledger.run(run_id,binding,ceiling)
            if manifest_path.exists():
                manifest = load_json(manifest_path)
                if manifest.get('binding') != binding or manifest.get('run_id') != run_id:
                    raise ImageError('output_binding_changed')
                for entry in [*manifest.get('positions',{}).values(),*manifest.get('candidates',{}).values()]:
                    if entry.get('file'): artifact(directory,entry['file'])
                if manifest.get('status') in ('complete','complete_with_test_waiver',BYPASS_STATUS):
                    document = load_json(directory/'document.json')
                    if document.get('article') != article or digest(document) != manifest.get('document_digest'):
                        raise ImageError('cached_document_content_changed')
                    paths = [str(artifact(directory,manifest['positions'][position]['file']))
                             for position in ['cover',*[f'body-{i}' for i in range(1,len(positions))]]]
                    if [document.get('cover_path'),*document.get('image_paths',[])] != paths:
                        raise ImageError('cached_document_paths_changed')
                    hashes_now = validate_files(document['cover_path'],document['image_paths'])
                    if hashes_now != [manifest['positions'][position]['sha']
                                      for position in ['cover',*[f'body-{i}' for i in range(1,len(positions))]]]:
                        raise ImageError('cached_selected_image_changed')
                    if manifest.get('status')==BYPASS_STATUS:
                        check_bypass_manifest(manifest,['cover',*[f'body-{i}' for i in range(1,len(positions))]],hashes_now)
                    verify_reviews(document,hashes_now,config=self.config)
                    return document
            else:
                manifest = {'run_id':run_id,'binding':binding,'status':'working','processing':VERSION,
                            'positions':{},'candidates':{},'usage':[]}
                write_atomic(manifest_path,manifest)
            await asyncio.to_thread(self.provider.verify_models)
            selected, visual, reviews = [],self.historical_hashes(),{}
            base = slug(article.get('slug') or article['title'])
            current_name = None
            if manifest.get('test_image_waiver'):
                if waiver is None:raise ImageError('explicit_staging_test_image_capability_required')
                stored=manifest['test_image_waiver']
                if any(stored.get(k)!=waiver[k] for k in waiver if k!='entries'):
                    raise ImageError('test_image_audit_scope_changed')
                waiver=stored
            def waive(position,name,entry):
                if waiver is None or not test_image_entry(self.config,waiver,position,entry):return None
                if entry.get('status')!='rejected' or not entry.get('intent') or not entry.get('review_intent') or not entry.get('reason'):
                    raise ImageError('test_image_candidate_review_required')
                for key in ('intent','review_intent'):
                    liability=self.ledger.get(entry[key])
                    if not liability or liability['run_id']!=run_id or liability['status'] not in ('settled','estimated'):
                        raise ImageError('test_image_candidate_requires_reconciliation')
                scores=entry.get('scores',{})
                if set(scores)!=set(('technical','anatomy','topic','brand','distinct')) or any(type(v) is not bool for v in scores.values()):
                    raise ImageError('test_image_candidate_scores_invalid')
                data=artifact(directory,entry['file']).read_bytes();sha,dh=hashes(data)
                expected_size=(1600,900) if position=='cover' else (1200,675)
                if (sha!=entry['sha'] or dh!=int(entry['dhash'],16) or len(data)>(200 if position=='cover' else 150)*1024 or
                        decode(data).size!=expected_size or any((dh^old).bit_count()<=6 for old in visual)):
                    raise ImageError('test_image_candidate_bytes_invalid')
                if 'test_image_waiver_archive' not in manifest:
                    raw=manifest_path.read_bytes();before_sha=hashlib.sha256(raw).hexdigest()
                    archive=directory/('manifest-before-test-waiver-'+before_sha+'.json')
                    if archive.exists():
                        if archive.is_symlink() or archive.read_bytes()!=raw:raise ImageError('test_image_archive_conflict')
                    else:
                        with archive.open('xb') as stream:
                            os.chmod(archive,0o600);stream.write(raw);stream.flush();os.fsync(stream.fileno())
                    manifest['test_image_waiver_archive']={'file':archive.name,'sha256':before_sha}
                waiver['entries'][position]={'candidate':name,'sha':sha,'scores':dict(scores),'reason':entry.get('reason',''),
                                            'model_status':'rejected','intent':entry['intent'],'review_intent':entry['review_intent']}
                manifest['test_image_waiver']=waiver
                manifest['automatic_flow_verdict']='HELD';manifest['model_quality_verdict']='FAIL'
                # Original candidate stays rejected. Only the separate operator selection is waived.
                return {**entry,'status':'operator_waived','source_candidate':name}
            try:
                for index,slot in enumerate(positions):
                    position = 'cover' if index == 0 else f'body-{index}'
                    entry = manifest['positions'].get(position)
                    if entry:
                        data = artifact(directory,entry['file']).read_bytes()
                        sha,dh = hashes(data)
                        waived=(entry.get('status')=='operator_waived' and waiver is not None and
                                test_image_entry(self.config,waiver,position,entry) and
                                waiver['entries'].get(position,{}).get('sha')==sha and
                                waiver['entries'][position].get('scores')==entry['scores'])
                        if bypass or entry.get('status')=='review_bypassed':
                            if (not bypass or entry.get('status')!='review_bypassed' or entry.get('scores') is not None
                                    or sha != entry['sha']):
                                raise ImageError('cached_selected_image_changed')
                        elif sha != entry['sha'] or (not all(entry['scores'].values()) and not waived):
                            raise ImageError('cached_selected_image_changed')
                        selected.append(entry);visual.append(dh);reviews[sha]=entry['scores'];continue
                    if waiver is not None and position=='cover':
                        choices=[(name,e) for name,e in manifest['candidates'].items()
                                 if name.startswith('cover-') and e.get('sha')==waiver.get('entries',{}).get('cover',{}).get('sha',
                                     test_image_cover_sha(self.config))]
                        if len(choices)!=1:raise ImageError('test_cover_candidate_missing_or_ambiguous')
                        found=waive(position,*choices[0])
                        if found is None:raise ImageError('test_cover_waiver_required')
                        manifest['positions'][position]=found;write_atomic(manifest_path,manifest)
                        selected.append(found);visual.append(int(found['dhash'],16));reviews[found['sha']]=found['scores'];continue
                    model = cfg.model_cover if index == 0 else cfg.model_body
                    size = cfg.cover_size if index == 0 else cfg.body_size
                    output_cap = cfg.output_ceiling if index == 0 else cfg.body_output_ceiling
                    prompt = generation_prompt(slot['brief'],index,slot['subject'])
                    # A bypassed review is never sent; outside a two-article day slot there is no pair hold to reserve.
                    skip_hold=bypass and not self.config.cloudflare.two_article_free_day
                    review_bound=self.provider.forecast(cfg.review_model,self.provider.review(b'',prompt,cfg.review_output_ceiling)) if is_cloudflare and not skip_hold else 0
                    found = None
                    for round_number in range(cfg.max_rounds):
                        candidates = []
                        for candidate in range(cfg.candidates):
                            name = f'{position}-r{round_number}-c{candidate}'
                            current_name = name
                            saved = manifest['candidates'].get(name)
                            if saved:
                                if saved.get('status') in ('rejected','operator_rejected'): continue
                                if saved.get('status') == 'approved' and not bypass: candidates.append(saved);continue
                                if saved.get('status') == 'review_bypassed' and bypass: candidates.append(saved);break
                                if saved.get('status') not in ('paused','review_paused'):
                                    raise ImageError('candidate_requires_reconciliation')
                            intent = digest({'binding':binding,'candidate':name,'operation':'generate'})
                            previous=[e for key,e in manifest['candidates'].items() if key.startswith(position+'-r')]
                            feedback=(next((f for e in reversed(previous) if (f:=rejection_feedback(e))),None)
                                      if revision_of and slot['subject']=='nail_editorial' else None)
                            effective=prompt+f' Candidate {candidate+1}, round {round_number+1}.'
                            if feedback:effective+=' FIXED CORRECTIONS (original brief remains authoritative): '+feedback['correction']
                            body = self.provider.generation(effective,size,output_cap)
                            if saved and saved.get('status') == 'review_paused':
                                filename = saved['file']
                                data = artifact(directory,filename).read_bytes()
                                sha,dh = hashes(data)
                                if sha != saved['sha'] or saved['intent'] != intent:
                                    raise ImageError('cached_selected_image_changed')
                                source_sha = saved['source_sha']
                            else:
                                manifest['candidates'][name] = {'status':'pending','intent':intent,'feedback':feedback,
                                                              'effective_generation_prompt':effective}
                                write_atomic(manifest_path,manifest)
                                envelope = await self.paid(run_id,intent,model,body,cfg.input_ceiling,output_cap,image=True,review_bound=review_bound)
                                try:
                                    raw = image_of(envelope)
                                    source_sha = hashlib.sha256(raw).hexdigest()
                                    processed = crop(raw,(1600,900) if index == 0 else (1200,675))
                                    if index == 0: processed = await self.cover_renderer(processed,article['title'])
                                    data = webp(processed,(200 if index == 0 else 150)*1024)
                                    sha,dh = hashes(data)
                                    if any((dh^old).bit_count() <= 6 for old in visual):
                                        raise ImageError('duplicate_image')
                                except ImageError as error:
                                    if is_cloudflare:self.ledger.release_pair(intent)
                                    manifest['candidates'][name] = {'status':'rejected','reason':str(error),'intent':intent}
                                    write_atomic(manifest_path,manifest);continue
                                filename = base+'-'+name+'.webp';write_bytes(directory/filename,data)
                            if bypass:
                                # TEMPORARY Owner decision: deterministic gates above already passed; no model review is made.
                                if is_cloudflare:self.ledger.release_pair(intent)
                                saved = {'status':'review_bypassed','file':filename,'sha':sha,
                                         'dhash':format(dh,'016x'),'scores':None,'reason':None,
                                         'intent':intent,'review_intent':None,'alt':slot['alt'],'caption':slot['caption'],
                                         'source_sha':source_sha,'model':model,'size':size,
                                         'prompt':prompt,'prompt_version':PROMPT_VERSION,'review_model':cfg.review_model,'rubric':REVIEW_VERSION,
                                         'effective_generation_prompt':body.get('prompt',prompt),
                                         'feedback':feedback,
                                         'reference':self.provider.reference_binding() if is_cloudflare and 'input_image_0' in body else None,
                                         'review_bypass':{'config_flag':BYPASS_FLAG,'rubric_skipped':REVIEW_VERSION}}
                                manifest['candidates'][name] = saved;write_atomic(manifest_path,manifest)
                                candidates.append(saved)
                                break
                            review_intent = digest({'binding':binding,'candidate':name,'operation':'review','sha':sha})
                            manifest['candidates'][name].update(file=filename,sha=sha,source_sha=source_sha,review_intent=review_intent)
                            write_atomic(manifest_path,manifest)
                            review_body = self.provider.review(data,prompt,cfg.review_output_ceiling)
                            answer = await self.paid(run_id,review_intent,cfg.review_model,review_body,
                                                     cfg.review_input_ceiling,cfg.review_output_ceiling,pair_intent=intent if is_cloudflare else None)
                            result = review_of(answer)
                            scores = result['scores']
                            # Deterministic checks are compulsory; a model verdict
                            # cannot turn a local bytes/hash failure into a PASS.
                            approved = all(scores.values())
                            saved = {'status':'approved' if approved else 'rejected','file':filename,'sha':sha,
                                     'dhash':format(dh,'016x'),'scores':scores,'reason':result['reason'],
                                     'intent':intent,'review_intent':review_intent,'alt':slot['alt'],'caption':slot['caption'],
                                     'source_sha':source_sha,'model':model,'size':size,
                                     'prompt':prompt,'prompt_version':PROMPT_VERSION,'review_model':cfg.review_model,'rubric':REVIEW_VERSION,
                                     'effective_generation_prompt':body.get('prompt',prompt),
                                     'feedback':feedback,
                                     'reference':self.provider.reference_binding() if is_cloudflare and 'input_image_0' in body else None}
                            manifest['candidates'][name] = saved;write_atomic(manifest_path,manifest)
                            if approved:
                                candidates.append(saved)
                                break
                            if waiver is not None:
                                waived=waive(position,name,saved)
                                if waived is not None:candidates.append(waived);break
                        if candidates:
                            found = candidates[0];break
                    if not found: raise ImageError('image_quality_held')
                    selected.append(found);visual.append(int(found['dhash'],16));reviews[found['sha']]=found['scores']
                    manifest['positions'][position]=found;write_atomic(manifest_path,manifest)
                image_paths = [str(directory/entry['file']) for entry in selected]
                verified_hashes = validate_files(image_paths[0],image_paths[1:])
                document = {'article':article,'cover_path':image_paths[0],'image_paths':image_paths[1:],
                            'image_hashes':verified_hashes,'image_reviews':reviews,
                            'images':[{'src':entry['file'],'alt':entry['alt'],'caption':entry['caption'],
                                       'width':1200,'height':675} for entry in selected[1:]],
                            'title':article['title'],'summary':article.get('summary',''),
                            'meta_title':article.get('meta_title',article.get('title_seo','')),
                            'meta_description':article.get('meta_description',''),'hashtags':article.get('hashtags',[])}
                if bypass:
                    document['review_bypass']={'enabled':True,'hashes':list(verified_hashes)}
                if waiver is not None:
                    document.update(test_image_waiver=waiver,automatic_flow_verdict='HELD',model_quality_verdict='FAIL')
                verify_reviews(document,verified_hashes,config=self.config)
                document['content_html'] = render_html(article,document['images'],site_origin=self.config.contract.site_origin)
                if is_cloudflare:document['quota_forecast']=self.ledger.forecast_report(run_id)
                write_atomic(directory/'document.json',document)
                preview = ('<!doctype html><html lang="vi"><meta charset="utf-8"><title>'+html.escape(article['title'])+
                           '</title><style>body{max-width:70ch;margin:2rem auto;padding:1rem;font:16px/1.75 Arial}img{max-width:100%;height:auto}</style>'+
                           '<h1>'+html.escape(article['title'])+'</h1><img alt="'+html.escape(positions[0]['alt'],quote=True)+
                           '" src="'+selected[0]['file']+'">'+document['content_html']+'</html>')
                write_bytes(directory/'preview.html',preview.encode())
                with self.state.connection() as db:
                    for entry in selected:
                        db.execute('INSERT OR IGNORE INTO image_selections VALUES(?,?,?)',
                                   (entry['sha'],entry['dhash'],self.clock()))
                if bypass:
                    manifest.update(review_bypass=bypass_block(['cover',*[f'body-{i}' for i in range(1,len(positions))]],verified_hashes),
                                    automatic_flow_verdict='REVIEW_BYPASSED')
                manifest.update(status=BYPASS_STATUS if bypass else 'complete_with_test_waiver' if waiver is not None else 'complete',artifacts_stale=False,usage=self.ledger.report(run_id),image_hashes=verified_hashes,
                                document_digest=digest(document))
                if is_cloudflare:manifest['quota_forecast']=self.ledger.forecast_report(run_id)
                manifest.pop('error',None)
                write_atomic(manifest_path,manifest)
                return document
            except BaseException as error:
                if is_cloudflare:manifest['quota_forecast']=self.ledger.forecast_report(run_id)
                if isinstance(error,DispatchPaused) and current_name:
                    entry = manifest['candidates'][current_name]
                    entry['status'] = 'review_paused' if entry.get('file') else 'paused'
                    entry['dispatch_pause'] = {'at':self.clock(),'request_sent':False}
                elif isinstance(error,Exception) and current_name and not is_cloudflare:
                    # A refusal or failure BEFORE any ledger reservation (budget refusal, or the free countTokens
                    # preflight failing with 429/timeout) sent no generation and cost nothing: resumable like a
                    # dispatch pause. With a reservation on the books the candidate keeps today's reconciliation path.
                    entry = manifest['candidates'][current_name]
                    key = 'review_intent' if entry.get('file') else 'intent'
                    if entry.get('status') == 'pending' and entry.get(key) and not self.ledger.get(entry[key]):
                        entry['status'] = 'review_paused' if entry.get('file') else 'paused'
                        entry['dispatch_pause'] = {'at':self.clock(),'request_sent':False,
                                                   'reason':'budget' if isinstance(error,BudgetExceeded) else 'predispatch_failure'}
                manifest.update(status='held',error=str(error) if isinstance(error,ImageError) else type(error).__name__,
                                usage=self.ledger.report(run_id))
                write_atomic(manifest_path,manifest)
                raise
