"""Subscription provenance and CLI-only dimensions; never opens the paid ledger."""
import hashlib
import html
import json
import re
from dataclasses import asdict
from pathlib import Path
from importlib.resources import files

from .._runtime import ProcessLock
from ..admin_images import validate_files, verify_reviews
from ..publication import digest
from ..writer import render_html
from . import ImageError
from .post import crop, decode, hashes, webp, thumbnail_cli
from .prompts import generation_prompt, feasible_brief, VERSION as PROMPT_VERSION
from .subscription import SubscriptionImages, verify_receipt, PROTOCOL

CLI_PROCESSING = 'subscription-native-cover1376x768-body1200x675-v1'


def validate_provenance(document, manifest, directory, config):
    from .pipeline import artifact, check_bypass_manifest, load_json
    from .subscription import read, uid, validate_request, verify_native_receipt
    if (config is None or config.image.provider != 'subscription' or config.contract.environment != 'staging' or
            manifest.get('provider') != 'subscription' or manifest.get('processing') != CLI_PROCESSING or
            manifest.get('document_digest') != digest(document) or manifest.get('article_sha') != digest(document.get('article')) or
            document.get('image_source') != {'route': 'subscription', 'api_usd': None, 'plan_cost_included': 'unknown'}):
        raise ImageError('cli_provenance_invalid')
    names = ['cover', *[f'body-{i}' for i in range(1, len(document.get('image_hashes', [])))]]
    actual = validate_files(document['cover_path'], document['image_paths'], config=config)
    if actual != document['image_hashes'] or set(manifest.get('positions', {})) != set(names):
        raise ImageError('cli_provenance_invalid')
    check_bypass_manifest(manifest, names, actual)
    for name, sha in zip(names, actual):
        entry = manifest['positions'][name]
        if str(artifact(directory, entry['file'])) != ([document['cover_path'], *document['image_paths']][names.index(name)]):
            raise ImageError('cli_provenance_paths_changed')
        if not re.fullmatch(re.escape(name) + r'-source-r[01]\.image', entry.get('source_file', '')):
            raise ImageError('cli_provenance_source_changed')
        original = directory / entry['source_file']
        if original.is_symlink() or any(p.is_symlink() for p in original.parents) or original.stat().st_size > 16 * 1024 * 1024:
            raise ImageError('cli_provenance_source_changed')
        receipt, request = entry['receipt'], entry['request']
        validate_request(request, entry.get('subscription_job', ''), config.subscription)
        queue = Path(config.subscription.queue_dir) / request['job_id']
        if (json.loads(read(queue / 'request.json', owner=uid(config.subscription.producer_user))) != request or
                json.loads(read(queue / 'receipt.json', owner=uid(config.subscription.worker_user))) != receipt):
            raise ImageError('cli_broker_receipt_changed')
        verify_receipt(receipt, request, original.read_bytes())
        verify_native_receipt(config.subscription, receipt, request, read(queue / 'native-record.bin', owner=uid(config.subscription.worker_user)),
                              read(queue / 'native-call.bin', owner=uid(config.subscription.worker_user)))
        if (request.get('binding') != manifest['binding'] or request.get('article_sha') != manifest['article_sha'] or
                request.get('prompt') != entry['prompt'] or receipt.get('request_sha') != digest(request) or
                receipt.get('job_id') != entry.get('subscription_job') or sha != entry['sha'] or
                hashes(artifact(directory, entry['file']).read_bytes())[0] != sha):
            raise ImageError('cli_provenance_invalid')
    verify_reviews(document, actual, config=config)
    return digest(manifest)


def reject_selected(pipeline, out, position, expected_sha, reason):
    """An explicit Owner veto can open the existing single retry; unknown jobs cannot."""
    from .pipeline import safe_directory, load_json, write_atomic, artifact
    directory = safe_directory(out)
    with ProcessLock(directory):
        path = directory / 'manifest.json'; manifest = load_json(path)
        entry = manifest.get('positions', {}).get(position)
        if (manifest.get('provider') != 'subscription' or not re.fullmatch(r'cover|body-[1-5]', position) or
                not entry or entry.get('sha') != expected_sha or hashes(artifact(directory, entry['file']).read_bytes())[0] != expected_sha or
                manifest.get('status') not in ('complete_with_review_bypass', 'owner_rejected')):
            raise ImageError('cli_owner_rejection_invalid')
        if manifest.get('revision', 0) >= 1 and manifest.get('status') != 'owner_rejected':
            raise ImageError('cli_single_owner_retry_spent')
        event = {'position': position, 'sha': expected_sha, 'reason': str(reason)[:500], 'at': pipeline.clock()}
        manifest.setdefault('operator_rejections', []).append(event)
        manifest['positions'].pop(position)
        manifest.update(revision=1, status='owner_rejected')
        manifest.pop('document_digest', None)
        write_atomic(path, manifest)
        return event


async def run(pipeline, article, out, run_id, *, enabled, paid_enabled, free_enabled):
    from .pipeline import slots, safe_directory, load_json, write_atomic, write_bytes, slug, bypass_block, BYPASS_STATUS, DispatchPaused
    config = pipeline.config
    if not enabled or paid_enabled or free_enabled:
        raise ImageError('explicit_subscription_enable_required')
    if (config.contract.environment != 'staging' or not config.image.review_bypass or
            config.image.candidates != 1 or config.image.max_rounds != 1):
        raise ImageError('cli_staging_bypass_single_generation_required')
    positions = slots(article)
    if len(positions) > config.subscription.max_images_per_run:
        raise ImageError('cli_run_image_cap')
    for slot in positions: feasible_brief(slot['brief'], slot['subject'])
    directory = safe_directory(out)
    binding = digest({'article': article, 'provider': 'subscription', 'subscription': asdict(config.subscription),
                      'image': asdict(config.image), 'processing': CLI_PROCESSING, 'prompt_version': PROMPT_VERSION,
                      'template': digest(files('nailblog.images').joinpath('templates/cover-cli.html').read_text(encoding='utf-8')),
                      'directory': str(directory)})
    provider = pipeline.provider
    with ProcessLock(directory):
        path = directory / 'manifest.json'
        if path.exists():
            manifest = load_json(path)
            if manifest.get('binding') != binding or manifest.get('run_id') != run_id:
                raise ImageError('output_binding_changed')
            if manifest.get('status') == BYPASS_STATUS:
                document = load_json(directory / 'document.json')
                validate_provenance(document, manifest, directory, config)
                return document
            if manifest.get('status') == 'held':
                raise ImageError('cli_job_requires_manual_reconciliation')
        else:
            manifest = {'provider': 'subscription', 'run_id': run_id, 'binding': binding, 'article_sha': digest(article),
                        'processing': CLI_PROCESSING, 'status': 'working', 'positions': {}, 'candidates': {}, 'usage': []}
            write_atomic(path, manifest)
        visual = pipeline.historical_hashes()
        names = ['cover', *[f'body-{i}' for i in range(1, len(positions))]]
        selected = []
        try:
            for index, (name, slot) in enumerate(zip(names, positions)):
                if name in manifest['positions']:
                    entry = manifest['positions'][name]
                    data = (directory / entry['file']).read_bytes()
                    sha, dh = hashes(data)
                    if sha != entry['sha']: raise ImageError('cached_selected_image_changed')
                    selected.append(entry); visual.append(dh); continue
                prompt = generation_prompt(slot['brief'], index, slot['subject'])
                pipeline.check_dispatch()
                revision = manifest.get('revision', 0)
                raw, receipt = await provider.generate(binding=binding, article_sha=digest(article), prompt=prompt,
                    run_id=run_id, position=name + '-r' + str(revision), before_dispatch=pipeline.check_dispatch)
                original = name + '-source-r' + str(revision) + '.image'
                write_bytes(directory / original, raw)
                size = (1376, 768) if index == 0 else (1200, 675)
                processed = crop(raw, size)
                if index == 0:
                    renderer = thumbnail_cli if pipeline.cover_renderer is pipeline.default_renderer else pipeline.cover_renderer
                    processed = await renderer(processed, article['title'])
                    if processed.size != size: raise ImageError('cli_cover_dimensions_changed')
                data = webp(processed, (200 if index == 0 else 150) * 1024)
                sha, dh = hashes(data)
                if any((dh ^ old).bit_count() <= 6 for old in visual): raise ImageError('duplicate_image')
                filename = slug(article.get('slug') or article['title']) + '-' + name + '-r' + str(revision) + '.webp'
                write_bytes(directory / filename, data)
                # Retain the exact request alongside its receipt; no API intent or zero-dollar invoice is fabricated.
                from .subscription import read, uid
                request = json.loads(read(Path(config.subscription.queue_dir) / receipt['job_id'] / 'request.json',
                                          owner=uid(config.subscription.producer_user)))
                entry = {'status': 'review_bypassed', 'file': filename, 'sha': sha, 'dhash': format(dh, '016x'),
                         'scores': None, 'review_intent': None, 'subscription_job': receipt['job_id'], 'receipt': receipt,
                         'request': request, 'source_file': original, 'source_sha': hashlib.sha256(raw).hexdigest(), 'prompt': prompt,
                         'alt': slot['alt'], 'caption': slot['caption'],
                         'review_bypass': {'config_flag': 'image.review_bypass', 'rubric_skipped': 'subscription-owner-grant'}}
                manifest['positions'][name] = entry
                manifest['candidates'][name] = entry
                selected.append(entry); visual.append(dh)
                write_atomic(path, manifest)
            paths = [str(directory / e['file']) for e in selected]
            checked_hashes = validate_files(paths[0], paths[1:], config=config)
            document = {'article': article, 'cover_path': paths[0], 'image_paths': paths[1:], 'image_hashes': checked_hashes,
                'image_reviews': {sha: None for sha in checked_hashes}, 'review_bypass': {'enabled': True, 'hashes': checked_hashes},
                'image_source': {'route': 'subscription', 'api_usd': None, 'plan_cost_included': 'unknown'},
                'images': [{'src': e['file'], 'alt': e['alt'], 'caption': e['caption'], 'width': 1200, 'height': 675} for e in selected[1:]],
                'title': article['title'], 'summary': article.get('summary', ''), 'meta_title': article.get('meta_title', article.get('title_seo', '')),
                'meta_description': article.get('meta_description', ''), 'hashtags': article.get('hashtags', [])}
            document['content_html'] = render_html(article, document['images'], site_origin=config.contract.site_origin)
            write_atomic(directory / 'document.json', document)
            preview = '<!doctype html><meta charset="utf-8"><h1>' + html.escape(article['title']) + '</h1><img src="' + selected[0]['file'] + '">' + document['content_html']
            write_bytes(directory / 'preview.html', preview.encode())
            manifest.update(status=BYPASS_STATUS, review_bypass=bypass_block(names, checked_hashes), automatic_flow_verdict='REVIEW_BYPASSED',
                            image_hashes=checked_hashes, document_digest=digest(document), subscription_billing=document['image_source'])
            write_atomic(path, manifest)
            validate_provenance(document, manifest, directory, config)
            with pipeline.state.connection() as db:
                for entry in selected:
                    db.execute('INSERT OR IGNORE INTO image_selections VALUES(?,?,?)', (entry['sha'], entry['dhash'], pipeline.clock()))
            return document
        except DispatchPaused as error:
            # A local refusal before enqueue sent no operation and is resumable.
            # Once queued, worker dispatch may have raced Owner pause: retain the
            # conservative manual-reconciliation gate even if cancellation exists.
            manifest.update(status='held' if error.dispatched else 'working', error=str(error))
            write_atomic(path, manifest)
            raise
        except BaseException as error:
            manifest.update(status='held', error=str(error) if isinstance(error, ImageError) else type(error).__name__)
            write_atomic(path, manifest)
            raise
