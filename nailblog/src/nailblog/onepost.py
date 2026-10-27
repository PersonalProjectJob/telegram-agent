"""Explicit one-post workflow. Scheduler/preview never opt into generation or website writes.

Each expensive boundary has a durable intent. Unknown text/generation/Create outcomes
are reconciled, never automatically repeated. Human image and Draft approvals name a
SHA-bound review artifact; neither approval is implied by a model's five booleans.
"""
import json
import html
import hashlib
import os
import tempfile
from datetime import datetime
from pathlib import Path

from . import facts
from .admin_images import validate_files, verify_reviews, test_image_audit, verify_test_operation
from .admin_state import AdminStore
from .images.pipeline import ImagePipeline, check_bypass_manifest, load_json
from .images import ImageError
from .publication import AdminError, binding, digest, row_fingerprint
from .quality import gate
from .schedule import VN_TZ
from .usage import write_atomic
from .writer import han_fields, hold_unexpected_han, placeholder_images, render_html, word_count


class OnePost:
    def __init__(self, service):
        self.service, self.state, self.config = service, service.state, service.config
        binding(self.config)
        with self.state.connection() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS onepost_runs(
                run_id TEXT PRIMARY KEY, binding TEXT NOT NULL, phase TEXT NOT NULL,
                document TEXT NOT NULL, post_id INTEGER UNIQUE, image_approval TEXT NOT NULL DEFAULT '')''')
            db.execute('''CREATE TABLE IF NOT EXISTS onepost_topic_claims(
                topic_id INTEGER PRIMARY KEY, run_id TEXT NOT NULL UNIQUE)''')
            for row in db.execute('SELECT run_id,document FROM onepost_runs').fetchall():
                topic_id = json.loads(row['document']).get('topic_id')
                if topic_id is not None:
                    existing = db.execute('SELECT run_id FROM onepost_topic_claims WHERE topic_id=?', (topic_id,)).fetchone()
                    if existing and existing['run_id'] != row['run_id']:
                        raise AdminError('legacy_topic_claim_conflict')
                    db.execute('INSERT OR IGNORE INTO onepost_topic_claims VALUES(?,?)', (topic_id,row['run_id']))

    def read(self, run_id):
        with self.state.connection() as db:
            row = db.execute('SELECT * FROM onepost_runs WHERE run_id=?', (run_id,)).fetchone()
            return dict(row) if row else None

    def store(self, run_id, phase, document):
        with self.state.connection() as db:
            db.execute('UPDATE onepost_runs SET phase=?,document=? WHERE run_id=?',
                       (phase, json.dumps(document, ensure_ascii=False), run_id))

    def active(self):
        if self.config.mode != 'live' or self.state.control()['paused']:
            raise AdminError('explicit_live_unpaused_required')

    @staticmethod
    def private_article_file(path, content):
        """Atomic private review artifacts; never replace a previous attempt's article."""
        if path.exists() or path.is_symlink():
            raise AdminError('article_artifact_already_exists')
        descriptor, temporary = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
                os.chmod(temporary, 0o600)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def retain_article(self, run_id, article, result, sources, directory, *, slot_at, repaired=(), override=False,
                       extra=None):
        """Commit completed text before filesystem work or a terminal quality error."""
        current = json.loads(self.read(run_id)['document'])
        saved = {**current, 'article': article, 'quality': result.as_dict(), 'slot_at': slot_at,
                 'facts': {'ungrounded': facts.check(article, sources)}, 'words': word_count(article),
                 'source_material': sources,
                 'repaired': list(repaired), 'writer_audit': list(article.get('writer_audit', [])),
                 'artifact_status': 'pending', **(extra or {})}
        phase = 'text_ready' if (result.ok or override) else 'held'
        self.store(run_id, phase, saved)
        try:
            if directory.is_symlink() or any(parent.is_symlink() for parent in directory.parents):
                raise AdminError('private_output_required')
            info = directory.stat()
            if os.name != 'nt' and (info.st_uid != os.getuid() or info.st_mode & 0o077):
                raise AdminError('private_output_required')
            content = render_html(article, placeholder_images(article), site_origin=self.config.contract.site_origin)
            preview = ('<!doctype html><html lang="vi"><head><meta charset="utf-8"><title>' +
                       html.escape(str(article.get('title', ''))) + '</title></head><body><h1>' +
                       html.escape(str(article.get('title', ''))) + '</h1>' + content + '</body></html>')
            assessment = {key: value for key, value in saved.items() if key != 'artifact_status'}
            self.private_article_file(directory/'article.json', json.dumps(assessment, ensure_ascii=False, indent=2))
            self.private_article_file(directory/'article.html', preview)
        except Exception as error:
            saved.update(artifact_status='failed', artifact_error_type=type(error).__name__)
            self.store(run_id, 'held', saved)
            raise AdminError('article_artifact_write_failed') from None
        saved.update(artifact_status='complete', article_json=str(directory/'article.json'),
                     article_html=str(directory/'article.html'),
                     article_artifact_hashes={name: hashlib.sha256((directory/name).read_bytes()).hexdigest()
                                             for name in ('article.json', 'article.html')})
        self.store(run_id, phase, saved)
        return saved

    @staticmethod
    def verify_article_artifacts(saved):
        # Legacy checkpoints have no artifact-status field. New incomplete text
        # must be reconciled, never regenerated or allowed to spend on images.
        if 'artifact_status' not in saved:
            return
        if saved['artifact_status'] != 'complete':
            raise AdminError('article_artifacts_require_reconciliation')
        root = Path(saved['out'])
        try:
            for name in ('article.json', 'article.html'):
                path = root/name
                if (not path.is_file() or path.is_symlink() or any(p.is_symlink() for p in path.parents) or
                        hashlib.sha256(path.read_bytes()).hexdigest() != saved['article_artifact_hashes'][name]):
                    raise ValueError()
                if os.name != 'nt' and (path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077):
                    raise ValueError()
            if json.loads((root/'article.json').read_text(encoding='utf-8'))['article'] != saved['article']:
                raise ValueError()
        except Exception:
            raise AdminError('article_artifacts_require_reconciliation') from None

    @staticmethod
    def provenance(document, directory, *, config=None):
        """An imported manual-curation preview is never an automatic quality PASS."""
        manifest = load_json(Path(directory)/'manifest.json')
        if manifest.get('provider') == 'subscription':
            from .images.subscription_pipeline import validate_provenance
            try: return validate_provenance(document, manifest, Path(directory), config)
            except ImageError as error: raise AdminError(str(error)) from None
        waived=bool(document.get('test_image_waiver'))
        # TEMPORARY Owner decision (nailblog/AGENTS.md): model image review bypass, only while [image] review_bypass is ON.
        bypassed='review_bypass' in document
        if bypassed:
            if waived:raise AdminError('review_bypass_test_waiver_conflict')
            bypass_hashes=document.get('image_hashes',[])
            verify_reviews(document,bypass_hashes,config=config)
            try:
                check_bypass_manifest(manifest,['cover',*[f'body-{i}' for i in range(1,len(bypass_hashes))]],bypass_hashes)
            except ImageError:
                raise AdminError('review_bypass_audit_invalid') from None
        if waived:
            verify_reviews(document,document.get('image_hashes',[]),config=config)
            test_image_audit(config,document['article'],document['test_image_waiver']['run_id'],
                             document['test_image_waiver']['binding'],directory)
            if (manifest.get('status')!='complete_with_test_waiver' or
                    manifest.get('test_image_waiver')!=document['test_image_waiver'] or
                    manifest.get('automatic_flow_verdict')!='HELD' or manifest.get('model_quality_verdict')!='FAIL'):
                raise AdminError('test_image_manifest_mismatch')
            archive=manifest.get('test_image_waiver_archive',{});name=archive.get('file','')
            if (name!='manifest-before-test-waiver-'+str(archive.get('sha256'))+'.json' or
                    len(str(archive.get('sha256')))!=64 or Path(name).name!=name):
                raise AdminError('test_image_manifest_archive_required')
            before_path=Path(directory)/name
            if before_path.is_symlink() or hashlib.sha256(before_path.read_bytes()).hexdigest()!=archive['sha256']:
                raise AdminError('test_image_manifest_archive_changed')
            before=load_json(before_path)
            if any(manifest.get('candidates',{}).get(k)!=v for k,v in before.get('candidates',{}).items()):
                raise AdminError('test_image_model_history_changed')
        if (document.get('status')=='manual_curated_preview' or manifest.get('document_digest')!=digest(document) or
                (not waived and not bypassed and (document.get('automatic_flow_verdict')=='HELD' or
                                 manifest.get('status')!='complete' or manifest.get('automatic_flow_verdict')=='HELD'))):
            raise AdminError('complete_reviewed_image_provenance_required')
        positions = manifest.get('positions', {})
        expected = ['cover', *[f'body-{i}' for i in range(1, len(document.get('image_hashes', [])))]]
        for position, sha in zip(expected, document.get('image_hashes', [])):
            entry = positions.get(position, {})
            if waived and entry.get('status')=='operator_waived':
                candidate=manifest.get('candidates',{}).get(entry.get('source_candidate'),{})
                event=document['test_image_waiver'].get('entries',{}).get(position,{})
                if (candidate.get('status')!='rejected' or event.get('candidate')!=entry.get('source_candidate') or
                        any(entry.get(k)!=candidate.get(k) for k in ('sha','scores','reason','intent','review_intent')) or
                        entry.get('sha')!=sha or event.get('sha')!=sha or event.get('scores')!=entry.get('scores')):
                    raise AdminError('test_image_selected_candidate_changed')
                continue
            if bypassed:
                if entry.get('status') != 'review_bypassed' or entry.get('sha') != sha or not entry.get('intent'):
                    raise AdminError('complete_reviewed_image_provenance_required')
                continue
            if (entry.get('status') != 'approved' or entry.get('sha') != sha or
                    not entry.get('intent') or not entry.get('review_intent') or
                    any(entry.get('scores', {}).get(k) is not True
                        for k in ('technical','anatomy','topic','brand','distinct'))):
                raise AdminError('complete_reviewed_image_provenance_required')
        return digest(manifest)

    async def text_jobs(self, run_id, topic, text, posts, today, before_dispatch):
        """The Agy text job(s) of one run: `write_agy` (at most 2 `ask` calls) in a worker thread.

        `unknown` (no native output) allows exactly ONE new job: the purpose that ended unknown gets a `#retry1`
        suffix, durably recorded BEFORE the new dispatch; a second `unknown` holds the run (`text_unknown`).
        `daily_cap`/`not_ready` and the other codes keep the purpose and surface as transient errors."""
        from .agy_text import AgyTextError
        from .images.pipeline import DispatchPaused
        from .text import TextModelError
        ask = text if text is not None else self.service.text_model()
        if not callable(ask) or hasattr(ask, 'ask'):
            raise AdminError('text_ask_required')

        def gate_dispatch():
            self.active()
            if before_dispatch is None:
                return
            try:
                stopped = before_dispatch() is False
            except Exception:
                raise DispatchPaused('workflow_paused_before_dispatch') from None
            if stopped:
                raise AdminError('dispatch_stopped')
        gate_dispatch()
        while True:
            retries = dict(json.loads(self.read(run_id)['document']).get('text_retry', {}))
            last = []

            def asker(prompt, schema, *, purpose, deadline=None, _retries=retries, _last=last):
                gate_dispatch()
                _last[:] = [purpose]
                suffix = '#retry' + str(_retries[purpose]) if _retries.get(purpose) else ''
                return ask(prompt, schema, purpose=purpose + suffix, deadline=deadline)
            try:
                return await self.service.write_agy_draft(asker, topic, posts=posts, today=today)
            except AgyTextError as error:
                if error.code == 'unknown':
                    if retries or not last:
                        self.store_progress(run_id, 'held', text_unknown=True)
                        raise AdminError('text_unknown') from None
                    self.store_progress(run_id, 'text_jobs', text_retry={last[0]: 1})
                    continue
                if error.code == 'held':
                    raise AdminError('text_held_by_worker') from None
                raise AdminError('text_' + error.code) from None
            except TextModelError as error:
                raise AdminError(str(getattr(error, 'code', '') or 'text_model_error')) from None

    def store_progress(self, run_id, phase, **fields):
        document = json.loads(self.read(run_id)['document'])
        for name, value in fields.items():
            document[name] = {**document.get(name, {}), **value} if isinstance(value, dict) else value
        self.store(run_id, phase, document)

    async def prepare(self, run_id, topic_id, out, *, text=None, article=None, images_document=None,
                      before_dispatch=None, subscription_enabled=True):
        """Text job(s) -> gate -> images. `text` is the Agy `ask(prompt, schema, *, purpose, deadline=None)` (sync);
        default `service.text_model()`. A run held by the gate with only overridable issues still gets its images and
        carries `requires_override` for the Owner cards; any other hold stops before images."""
        self.active()
        if not isinstance(run_id, str) or not run_id.strip() or len(run_id) > 160:
            raise AdminError('invalid_run_id')
        directory = Path(out)
        if not directory.is_absolute() or directory.is_symlink():
            raise AdminError('private_output_required')
        topic = self.state.get_topic(topic_id)
        if not topic or topic['state'] != 'approved':
            raise AdminError('approved_topic_required')
        cache = self.service.load_cache()
        if cache is None:
            raise AdminError('published_list_required')
        image_input = Path(images_document).resolve() if images_document else None
        image_data = load_json(image_input) if image_input else None
        if image_data is not None:
            verify_test_operation(image_data,self.config,run_id=run_id,slot_at=topic['slot_at'])
            self.provenance(image_data, image_input.parent,config=self.config)
        identity = digest({'environment': binding(self.config), 'topic': topic, 'out': str(directory.resolve()),
                           'article': article, 'image_input': str(image_input) if image_input else None,
                           'image_document_digest': digest(image_data) if image_data else None,
                           'subscription_enabled': bool(subscription_enabled)})
        with self.state.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT * FROM onepost_runs WHERE run_id=?', (run_id,)).fetchone()
            if current and current['binding'] != identity:
                raise AdminError('onepost_binding_changed')
            claim = db.execute('SELECT run_id FROM onepost_topic_claims WHERE topic_id=?', (topic_id,)).fetchone()
            if claim and claim['run_id'] != run_id:
                raise AdminError('topic_already_claimed')
            if not claim:
                db.execute('INSERT INTO onepost_topic_claims VALUES(?,?)', (topic_id, run_id))
            if not current:
                if db.execute('''SELECT 1 FROM onepost_runs r WHERE NOT EXISTS(
                        SELECT 1 FROM onepost_topic_claims c WHERE c.run_id=r.run_id)''').fetchone():
                    raise AdminError('legacy_onepost_requires_reconciliation')
                if db.execute('SELECT 1 FROM posts WHERE topic_id=?', (topic_id,)).fetchone():
                    raise AdminError('topic_already_has_post')
                db.execute('INSERT INTO onepost_runs(run_id,binding,phase,document) VALUES(?,?,?,?)',
                           (run_id, identity, 'text_jobs', json.dumps({'topic_id':topic_id,'out':str(directory.resolve())})))
            elif current['phase'] in ('text_pending', 'text_paused'):
                # Agy jobs are keyed by (purpose, prompt): re-entering reattaches to the same job, never dispatches twice.
                db.execute('UPDATE onepost_runs SET phase=? WHERE run_id=?', ('text_jobs',run_id))
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        current = self.read(run_id)
        saved = json.loads(current['document'])
        if saved.get('article') and han_fields(saved['article']):
            raise AdminError('unexpected_han_requires_reconciliation')
        if current['phase'] == 'text_jobs':
            draft = None
            today = datetime.fromtimestamp(self.service.clock(), VN_TZ).date()
            sources = json.loads(topic['sources_json'])
            sources = sources.get('sources', []) if isinstance(sources, dict) else sources
            if article is None:
                draft = await self.text_jobs(run_id, topic, text, cache['posts'], today, before_dispatch)
                article, result = draft.article, draft.result
                overridable = draft.held and draft.can_override
                extra = {'text_dispatches': draft.asks}
                if overridable:
                    extra.update(requires_override=True, override_issues=draft.issues)
                elif draft.held:
                    extra.update(text_issues=draft.issues)
            else:
                result = gate(article, category=topic['category'], existing_slugs=[p.get('slug') for p in cache['posts']],
                              posts=cache['posts'], today=today, ungrounded=facts.check(article, sources),
                              site_origin=self.config.contract.site_origin)
                result = hold_unexpected_han(article, result)
                overridable, extra = False, {}
            saved = self.retain_article(run_id, article, result, sources, directory, slot_at=topic['slot_at'],
                                        repaired=getattr(draft, 'repaired', ()), override=overridable, extra=extra)
            if not result.ok and not overridable:
                raise AdminError('text_quality_held' if draft is not None else 'quality_pass_required')
            self.store(run_id, 'text_ready', saved)
        elif current['phase'] == 'held':
            raise AdminError('onepost_held')
        elif current['phase'] in ('images_ready', 'images_approved', 'draft_verified', 'scheduled', 'published'):
            self.verify_images(saved,config=self.config)
            return self.artifact(run_id)
        article = saved['article']
        self.verify_article_artifacts(saved)
        if image_data is None:
            # Exact limits of this generation, so a one-time retry can never raise them.
            saved['image_run'] = {'subscription_enabled': bool(subscription_enabled)}
        self.store(run_id, 'images_pending', saved)
        if image_data is None:
            image_data = await ImagePipeline(self.config, before_dispatch=self.active).run(
                article, directory/'images', run_id+'-images', subscription_enabled=bool(subscription_enabled))
            image_base = directory/'images'
        else:
            image_base = image_input.parent
        self.attach_images(saved, article, image_data, image_base, image_input)
        with self.state.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT post_id FROM onepost_runs WHERE run_id=?', (run_id,)).fetchone()
            post_id = row['post_id']
            if post_id is None:
                # Create the local post and bind its run in the SAME transaction.
                if db.execute('SELECT 1 FROM posts WHERE topic_id=?', (topic_id,)).fetchone():
                    raise AdminError('topic_already_has_post')
                post_id = db.execute('INSERT INTO posts(topic_id,slot_at,state,draft_json,fingerprint) VALUES(?,?,?,?,?)',
                                    (topic_id, topic['slot_at'], 'writing', '{}', '')).lastrowid
                db.execute('UPDATE onepost_runs SET post_id=? WHERE run_id=?', (post_id, run_id))
        self.store(run_id, 'images_ready', saved)
        return self.artifact(run_id)

    def attach_images(self, saved, article, image_data, image_base, image_input):
        """Validate a finished image document and copy it into the run document."""
        if image_data.get('article') != article:
            raise AdminError('image_article_mismatch')
        provenance = self.provenance(image_data, image_base,config=self.config)
        paths = [image_data.get('cover_path') or str(image_base/'cover.webp'),
                 *(image_data.get('image_paths') or [str(image_base/i['src']) for i in image_data.get('images', [])])]
        paths = [str(Path(p).resolve()) for p in paths]
        if any(not Path(p).is_relative_to(image_base.resolve()) for p in paths):
            raise AdminError('image_path_outside_document')
        hashes = validate_files(paths[0], paths[1:], config=self.config)
        verify_reviews(image_data, hashes,config=self.config)
        saved.update(cover_path=paths[0], image_paths=paths[1:], image_hashes=hashes,
                     image_reviews=image_data['image_reviews'], image_metadata=image_data['images'],
                     image_provenance=provenance, image_document=str(image_input or (image_base/'document.json').resolve()),
                     image_document_digest=digest(image_data), image_quota_forecast=image_data.get('quota_forecast'))
        if image_data.get('test_image_waiver'):
            saved.update(test_image_waiver=image_data['test_image_waiver'],automatic_flow_verdict='HELD',model_quality_verdict='FAIL')
        if 'review_bypass' in image_data:
            saved.update(review_bypass=image_data['review_bypass'])
        if 'image_source' in image_data:
            saved['image_source'] = image_data['image_source']

    @staticmethod
    def verify_images(document, *, config=None):
        OnePost.verify_article_artifacts(document)
        source = load_json(Path(document['image_document']))
        if (digest(source) != document['image_document_digest'] or
                OnePost.provenance(source, Path(document['image_document']).parent,config=config) != document['image_provenance']):
            raise AdminError('image_provenance_changed')
        hashes = validate_files(document['cover_path'], document['image_paths'], config=config)
        verify_reviews(document, hashes,config=config)
        return hashes

    def artifact(self, run_id):
        run = self.read(run_id)
        if not run:
            raise AdminError('unknown_onepost_run')
        saved = json.loads(run['document'])
        review = {'run_id': run_id, 'post_id': run['post_id'], 'phase': run['phase'],
                  'environment': binding(self.config), 'article': saved.get('article'),
                  'quality': saved.get('quality'), 'image_hashes': saved.get('image_hashes'),
                  'images': saved.get('image_metadata'), 'image_quota_forecast': saved.get('image_quota_forecast')}
        if saved.get('test_image_waiver'):
            review.update(test_image_waiver=saved['test_image_waiver'],automatic_flow_verdict='HELD',model_quality_verdict='FAIL')
        if saved.get('review_bypass'):
            review.update(review_bypass=saved['review_bypass'],automatic_flow_verdict='REVIEW_BYPASSED')
        if saved.get('image_source'):
            review['image_source'] = saved['image_source']
        if saved.get('requires_override'):
            review.update(requires_override=True, issues=saved.get('override_issues') or [])
        review.update(facts=saved.get('facts'), words=saved.get('words'), repaired=saved.get('repaired'),
                      writer_audit=saved.get('writer_audit'), text_dispatches=saved.get('text_dispatches'),
                      artifact_status=saved.get('artifact_status'))
        if run['post_id']:
            row = self.state.get_post(run['post_id'])
            if row['admin_id']:
                review['draft_fingerprint'] = row_fingerprint(row, self.config)
                review['admin_id'] = row['admin_id']
        result = {'snapshot': digest(review), **review}
        if saved.get('out'):
            write_atomic(Path(saved['out'])/'approval.json', result)
        return result

    def accept_images(self, run_id, snapshot):
        self.active()
        run = self.read(run_id)
        if not run or run['phase'] != 'images_ready' or self.artifact(run_id)['snapshot'] != snapshot:
            raise AdminError('image_approval_snapshot_changed')
        saved = json.loads(run['document'])
        verify_test_operation(saved,self.config,run_id=run_id,slot_at=saved.get('slot_at'),state=self.state,post_id=run['post_id'])
        if han_fields(saved['article']):
            raise AdminError('unexpected_han_requires_review')
        self.verify_images(saved,config=self.config)
        with self.state.connection() as db:
            db.execute('UPDATE onepost_runs SET image_approval=?,phase=? WHERE run_id=? AND phase=?',
                       (digest({'article': saved['article'], 'hashes': saved['image_hashes']}),
                        'images_approved', run_id, 'images_ready'))
        return self.artifact(run_id)

    async def save(self, run_id, category_id):
        self.active()
        run = self.read(run_id)
        if not run or run['phase'] not in ('images_approved', 'draft_verified'):
            raise AdminError('manual_image_acceptance_required')
        saved = json.loads(run['document'])
        verify_test_operation(saved,self.config,run_id=run_id,slot_at=saved.get('slot_at'),state=self.state,post_id=run['post_id'])
        if AdminStore(self.state, self.config).operation(run['post_id']) is None and han_fields(saved['article']):
            raise AdminError('unexpected_han_requires_review')
        self.verify_images(saved,config=self.config)
        if run['image_approval'] != digest({'article': saved['article'], 'hashes': saved['image_hashes']}):
            raise AdminError('image_approval_changed')
        article = saved['article']
        # Pre-upload URLs are local identities on the pinned API origin, never placeholders sent to Create.
        images = [{**i, 'src': self.config.contract.api_origin+'/nailblog-local/'+h+'.webp'}
                  for i, h in zip(saved['image_metadata'], saved['image_hashes'][1:])]
        content = render_html(article, images, site_origin=self.config.contract.site_origin)
        payload = {'categoryId': category_id, 'status': 'Draft', 'publishedAt': saved['slot_at'],
                   'isPublic': True, 'isFeatured': False, 'hashtags': article['hashtags'],
                   'coverImage': {'imageUrl': self.config.contract.api_origin+'/nailblog-local/'+saved['image_hashes'][0]+'.webp'},
                   'images': [{'imageUrl': i['src']} for i in images],
                   'translations': [{'languageCode': 'VI', 'title': article['title'], 'summary': article['summary'],
                                     'content': content, 'metaTitle': article.get('meta_title', article['title_seo']),
                                     'metaDescription': article['meta_description']}]}
        store = AdminStore(self.state, self.config)
        if store.operation(run['post_id']) is None:
            document = {**article, 'quality': saved['quality'], 'content_html': content, 'admin_category_id': category_id,
                        'image_hashes': saved['image_hashes'], 'image_reviews': saved['image_reviews']}
            if saved.get('test_image_waiver'):
                document.update(test_image_waiver=saved['test_image_waiver'],automatic_flow_verdict='HELD',model_quality_verdict='FAIL')
            if saved.get('review_bypass'):
                document.update(review_bypass=saved['review_bypass'])
            self.state.set_post_document(run['post_id'], document)
        actual = await self.service.save_remote_draft(run['post_id'], payload, cover_path=saved['cover_path'],
                                                      image_paths=saved['image_paths'])
        self.store(run_id, 'draft_verified', saved)
        return self.artifact(run_id)

    def approve(self, run_id, snapshot):
        self.active()
        run = self.read(run_id)
        if not run or run['phase'] != 'draft_verified' or self.artifact(run_id)['snapshot'] != snapshot:
            raise AdminError('draft_approval_snapshot_changed')
        if han_fields(json.loads(run['document'])['article']):
            raise AdminError('unexpected_han_requires_review')
        saved=json.loads(run['document'])
        verify_test_operation(saved,self.config,run_id=run_id,slot_at=saved.get('slot_at'),state=self.state,post_id=run['post_id'])
        AdminStore(self.state, self.config).approve(run['post_id'], self.service.clock())
        self.store(run_id, 'scheduled', json.loads(run['document']))
        return self.artifact(run_id)

    async def publish(self, run_id):
        self.active()
        run = self.read(run_id)
        if not run or run['phase'] not in ('scheduled', 'published'):
            raise AdminError('approved_onepost_required')
        if self.state.get_post(run['post_id'])['state'] == 'scheduled' and han_fields(json.loads(run['document'])['article']):
            raise AdminError('unexpected_han_requires_review')
        if self.state.get_post(run['post_id'])['state']=='scheduled' and json.loads(run['document']).get('test_image_waiver'):
            saved=json.loads(run['document'])
            verify_test_operation(saved,self.config,run_id=run_id,slot_at=saved.get('slot_at'),state=self.state,post_id=run['post_id'])
            self.verify_images(json.loads(run['document']),config=self.config)
        if self.state.get_post(run['post_id'])['state'] != 'published':
            await self.service.publish_pass(post_id=run['post_id'])
        if self.state.get_post(run['post_id'])['state'] == 'published':
            self.store(run_id, 'published', json.loads(run['document']))
        return self.artifact(run_id)

    async def retry_images(self, run_id, reason):
        """One operator image reject: veto every selected image, rerun the SAME image run once, never raise a limit."""
        self.active()
        run = self.read(run_id)
        if not run or run['phase'] != 'images_ready' or run['image_approval']:
            raise AdminError('image_retry_not_allowed')
        saved = json.loads(run['document'])
        if saved.get('image_retries', 0) >= 1:
            raise AdminError('image_retry_exhausted')
        limits = saved.get('image_run')
        if not isinstance(limits, dict) or not saved.get('out') or not saved.get('image_hashes'):
            raise AdminError('image_retry_not_allowed')
        reason = ''.join(c for c in ' '.join(str(reason or '').split()) if c.isprintable())[:500] or 'operator_rejected_images'
        directory = Path(saved['out'])
        hashes = saved['image_hashes']
        positions = ['cover', *[f'body-{i}' for i in range(1, len(hashes))]]
        pipeline = ImagePipeline(self.config, before_dispatch=self.active)
        # The single retry is spent durably BEFORE any veto: a crash can resume, never retry again.
        saved.update(image_retries=1, image_retry_reasons=[*saved.get('image_retry_reasons', []), reason])
        self.store(run_id, 'images_pending', saved)
        for position, sha in zip(positions, hashes):
            pipeline.reject_selected(directory/'images', position, sha, reason)
        image_data = await pipeline.run(saved['article'], directory/'images', run_id+'-images',
                                        subscription_enabled=bool(limits.get('subscription_enabled')))
        self.attach_images(saved, saved['article'], image_data, directory/'images', None)
        self.store(run_id, 'images_ready', saved)
        return self.artifact(run_id)

    def mark_published(self, run_id):
        """Record an already-published post on its run. Never calls admin or publish."""
        run = self.read(run_id)
        if not run or not run['post_id'] or self.state.get_post(run['post_id'])['state'] != 'published':
            raise AdminError('mark_published_requires_published')
        self.store(run_id, 'published', json.loads(run['document']))
        return self.artifact(run_id)
