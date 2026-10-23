"""`image-compare`: a bounded, owner-triggered side-by-side of Gemini image models on the production prompts.

One cover prompt (position 0) and one body prompt (position 1) are built exactly as the pipeline builds them, then each
`model@size` variant generates both through the real PaidLedger. No review model, website, browser or Telegram is used.
A result is never upscaled: an image too small for the production crop is shown as FAIL with its real size.
"""
import base64
import html
import json
import logging
import re
import secrets
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from .._runtime import ProcessLock
from ..model_lock import MODEL_LOCK
from ..paid import PaidLedger, micros
from ..publication import digest
from ..state import State
from ..usage import UsageRecorder, day_bounds
from . import ImageError
from .pipeline import STALE_MARGIN_S, safe_directory, slots, write_bytes
from .post import crop, decode
from .prompts import feasible_brief, generation_prompt
from .provider import GeminiImages, image_of, parts_of, usage
from ..gemini_text import MODEL_NAME

LOG = logging.getLogger(__name__)

SIZES = ('1K', '2K', '4K')
TARGETS = {'cover': (1600, 900), 'body': (1200, 675)}
SCHEMA = 1


def parse_variants(text):
    """`m1@2K,m2@1K` -> [(model, size), ...]; duplicates and unknown tiers are refused."""
    result = []
    for item in str(text or '').split(','):
        model, separator, size = item.strip().partition('@')
        if not separator or not MODEL_NAME.fullmatch(model) or size not in SIZES:
            raise ImageError('invalid_variant_expected_model_at_1K_2K_4K')
        if (model, size) in result:
            raise ImageError('duplicate_variant')
        result.append((model, size))
    if not result:
        raise ImageError('variants_required')
    return result


def prompts_for(article):
    """The production cover and first body prompts, from the same slot derivation the pipeline uses."""
    positions = slots(article)
    result = []
    for index in (0, 1):
        slot = positions[index]
        feasible_brief(slot['brief'], slot['subject'])
        result.append(generation_prompt(slot['brief'], index, slot['subject']))
    return result


def _mime(raw):
    if raw.startswith(b'\x89PNG'):
        return 'image/png'
    if raw.startswith(b'\xff\xd8'):
        return 'image/jpeg'
    return 'image/webp'


def _daily_left_micros(ledger, now):
    """What is left of today's (Vietnam day) cap: settled usage plus every non-settled reservation is already spent."""
    spent = ledger.state.usd_between(*day_bounds(now))
    with ledger.state.connection() as db:
        active = db.execute("SELECT COALESCE(SUM(reserved),0) FROM paid_intents WHERE status!='settled'").fetchone()[0]
    return micros(ledger.config.budget.daily_usd) - micros(spent) - active


def _run_used_micros(ledger, run_id):
    return sum(row['charged'] if row['status'] == 'settled' else row['reserved'] for row in ledger.report(run_id))


def _prices(config, model, size, output_cap):
    """(bound_usd, price_in_per_m, output_rate_per_m, unit_price) with the pipeline's fail-closed price rules."""
    price_in = config.budget.text_prices.get(model)
    rate = config.budget.image_output_prices.get(model)
    if price_in is None or price_in[0] <= 0 or price_in[1] <= 0:
        raise ImageError('positive_model_price_required')
    if not isinstance(rate, (int, float)) or rate <= 0 or rate < price_in[1]:
        raise ImageError('positive_output_price_required')
    bound = (config.image.input_ceiling * price_in[0] + output_cap * rate) / 1e6
    unit = config.budget.image_unit_prices.get(model, {}).get(size)
    if unit is not None:
        bound = max(bound, unit)
    return bound, price_in, rate, unit


def _record(role, model, size):
    return {'role': role, 'model': model, 'size': size, 'status': 'not_run', 'target': list(TARGETS[role]),
            'pixels': None, 'crop_ok': None, 'tokens': None, 'usd': 0.0, 'reason': '', 'mime': '', 'image': None}


def _generate(ctx, provider, check, record, prompt, intent, output_cap):
    """Reserve, call, settle one image. Returns False when the run must stop (budget) before any transport call."""
    config, ledger = ctx['config'], ctx['ledger']
    model, size = record['model'], record['size']
    try:
        bound, price_in, rate, unit = _prices(config, model, size, output_cap)
    except ImageError as error:
        record.update(status='error', reason=str(error))
        return True
    needed = micros(bound)
    if needed > ctx['ceiling'] - _run_used_micros(ledger, ctx['run_id']):
        record.update(status='blocked', reason='max_usd_would_be_exceeded')
        return False
    if needed > _daily_left_micros(ledger, ctx['clock']()):
        record.update(status='blocked', reason='daily_cap_would_be_exceeded')
        return False
    if check.get('failed'):
        record.update(status='error', reason=check['failed'])
        return True
    if not check.get('done'):   # ListModels runs only once a call is actually affordable
        check['done'] = True
        try:
            provider.verify_models()
        except Exception as error:
            check['failed'] = 'model_check_failed:' + (str(error) if isinstance(error, ImageError) else type(error).__name__)
            record.update(status='error', reason=check['failed'])
            return True
    body = provider.generation(prompt, size, output_cap)
    try:
        ledger.reserve(intent, 'gemini', model, bound, run_id=ctx['run_id'])
    except Exception as error:
        record.update(status='blocked', reason='ledger_refused_' + type(error).__name__)
        return False
    try:
        with MODEL_LOCK.enter(background=True):
            envelope = provider.complete(model, body)
        parts_of(envelope)
        tokens_in, tokens_out = usage(envelope)
        meta = envelope['usageMetadata']
        record['tokens'] = {'prompt': meta['promptTokenCount'], 'candidates': meta['candidatesTokenCount'],
                            'thoughts': meta.get('thoughtsTokenCount', 0)}
        if tokens_in > config.image.input_ceiling or tokens_out > output_cap:
            raise ImageError('reported_usage_exceeds_reserved_bound')
        cost = (tokens_in * price_in[0] + tokens_out * rate) / 1e6
        metadata = {'input': tokens_in, 'output_including_thinking': tokens_out,
                    'cost_basis': 'all_output_at_conservative_rate', 'output_per_m': rate, 'tool': 'image-compare'}
        if unit is not None:
            metadata['unit_price_basis'] = {'token_cost_usd': cost, 'unit_price_usd': unit, 'size': size,
                                            'settled_usd': max(cost, unit)}
            cost = max(cost, unit)
        ledger.settle(intent, sum('inlineData' in part for part in parts_of(envelope)), cost, metadata)
        record['usd'] = ledger.get(intent)['charged'] / 1_000_000
        ctx['recorder'].publish()
    except BaseException as error:
        # Same rule as the pipeline: once dispatched, an unsettled call keeps its full bound until reconciled.
        if (ledger.get(intent) or {}).get('status') == 'pending':
            ledger.uncertain(intent)
        if not isinstance(error, Exception):
            raise
        record.update(status='error', reason=str(error) if isinstance(error, ImageError) else type(error).__name__)
        return True
    try:
        raw = image_of(envelope)
        record['mime'] = _mime(raw)
        record['pixels'] = list(decode(raw).size)
        record['image'] = raw
        try:
            crop(raw, TARGETS[record['role']])
            record.update(status='ok', crop_ok=True)
        except ImageError:
            record.update(status='fail_crop', crop_ok=False, reason='source_resolution_insufficient')
    except ImageError as error:
        record.update(status='error', reason=str(error))
    return True


def _board_json(ctx, rows, finished):
    ledger = ctx['ledger']
    settled = sum(row['charged'] for row in ledger.report(ctx['run_id']) if row['status'] == 'settled') / 1_000_000
    results = [{key: value for key, value in record.items() if key != 'image'} for record in rows]
    return {'schema': SCHEMA, 'run_id': ctx['run_id'], 'finished': finished, 'max_usd': ctx['max_usd'],
            'run_ceiling_usd': ctx['ceiling'] / 1_000_000, 'daily_usd': ctx['config'].budget.daily_usd,
            'total_usd': round(sum(record['usd'] for record in rows), 6),
            'ledger_settled_usd': round(settled, 6), 'results': results}


def _board_html(board, rows):
    out = ['<!doctype html><meta charset="utf-8"><title>image-compare ', html.escape(board['run_id']), '</title>',
           '<style>body{font-family:sans-serif;margin:16px}table{border-collapse:collapse}td{vertical-align:top;'
           'border:1px solid #ccc;padding:8px}.fail{color:#b00020;font-weight:bold}.ok{color:#146c2e}'
           'img{max-width:100%;height:auto}</style><h1>image-compare ', html.escape(board['run_id']), '</h1>',
           '<p>Total settled USD: <b>%.6f</b> (ledger %.6f) &middot; run ceiling %.6f &middot; daily cap %.2f</p>'
           % (board['total_usd'], board['ledger_settled_usd'], board['run_ceiling_usd'], board['daily_usd']),
           '<table><tr><th>Variant</th><th>Cover 1600x900</th><th>Body 1200x675</th></tr>']
    by_variant = {}
    for record in rows:
        by_variant.setdefault((record['model'], record['size']), {})[record['role']] = record
    for (model, size), pair in by_variant.items():
        out.append('<tr><td><b>%s</b><br>%s</td>' % (html.escape(model), html.escape(size)))
        for role in ('cover', 'body'):
            record = pair.get(role)
            if record is None:
                out.append('<td>-</td>')
                continue
            label = {'ok': 'OK', 'fail_crop': 'FAIL'}.get(record['status'], record['status'].upper())
            css = 'ok' if record['status'] == 'ok' else 'fail'
            tokens = record['tokens'] or {}
            pixels = 'x'.join(map(str, record['pixels'])) if record['pixels'] else 'n/a'
            out.append('<td><div class="%s">%s</div><div>real size: %s</div><div>crop without upscale: %s</div>'
                       '<div>tokens prompt/candidates/thoughts: %s/%s/%s</div><div>USD: %.6f</div>'
                       % (css, html.escape(label), html.escape(pixels),
                          {True: 'yes', False: 'NO', None: 'n/a'}[record['crop_ok']], tokens.get('prompt', '-'),
                          tokens.get('candidates', '-'), tokens.get('thoughts', '-'), record['usd']))
            if record['reason']:
                out.append('<div>' + html.escape(record['reason']) + '</div>')
            if record['image']:
                out.append('<img src="data:%s;base64,%s" width="%d" height="%d" alt="">'
                           % (record['mime'], base64.b64encode(record['image']).decode(),
                              record['pixels'][0], record['pixels'][1]))
            out.append('</td>')
        out.append('</tr>')
    out.append('</table>')
    return ''.join(out)


def _write_board(ctx, rows, finished):
    board = _board_json(ctx, rows, finished)
    write_bytes(ctx['directory'] / 'board.html', _board_html(board, rows).encode('utf-8'))
    write_bytes(ctx['directory'] / 'board.json', json.dumps(board, ensure_ascii=False, indent=2).encode('utf-8'))
    return board


def run_compare(config, article, out, variants, max_usd, *, paid_enabled=False, clock=time.time, state=None,
                transport=None, key_reader=None):
    """Run the variants, write `out/board.html` + `out/board.json`, return the board dict. Synchronous."""
    if paid_enabled is not True:
        raise ImageError('paid_enabled_required')
    if config.image.provider != 'gemini':
        raise ImageError('gemini_provider_required')
    if isinstance(max_usd, bool) or not isinstance(max_usd, (int, float)) or not 0 < max_usd:
        raise ImageError('positive_max_usd_required')
    prompts = dict(zip(('cover', 'body'), prompts_for(article)))
    directory = safe_directory(out)
    with ProcessLock(directory):   # two compare runs on one --out would overwrite each other's board
        return _run_locked(config, article, directory, variants, max_usd, prompts, clock, state, transport, key_reader)


def _run_locked(config, article, directory, variants, max_usd, prompts, clock, state, transport, key_reader):
    state = state or State(config.db_path)
    ledger = PaidLedger(state, config, clock)
    now = clock()
    # Crash recovery normally runs inside ImagePipeline.run only; do it before any reservation here too.
    ledger.reconcile_stale(now, config.image.timeout_seconds + STALE_MARGIN_S)
    ledger.age_out_uncertain(now)
    run_id = 'compare-' + datetime.fromtimestamp(now, timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + secrets.token_hex(3)
    ceiling = min(micros(max_usd), max(0, _daily_left_micros(ledger, now)))
    if ceiling > 0:
        ledger.run(run_id, digest({'tool': 'image-compare', 'variants': variants, 'title': article.get('title')}),
                   ceiling / 1_000_000)
    ctx = {'config': config, 'ledger': ledger, 'run_id': run_id, 'ceiling': ceiling, 'clock': clock,
           'max_usd': max_usd, 'directory': directory, 'recorder': UsageRecorder(state, config, clock=clock)}
    rows, stopped = [], ceiling <= 0
    kwargs = {name: value for name, value in (('transport', transport), ('key_reader', key_reader)) if value is not None}
    for number, (model, size) in enumerate(variants):
        pair = [_record('cover', model, size), _record('body', model, size)]
        rows.extend(pair)
        if stopped:
            for record in pair:
                record.update(status='blocked', reason='stopped_before_call')
            continue
        variant_config = replace(config, image=replace(config.image, model_cover=model, model_body=model,
                                                       review_model=model))
        provider = GeminiImages(variant_config, **kwargs)
        ctx['config'] = variant_config
        check = {}
        for record in pair:
            role = record['role']
            cap = config.image.output_ceiling if role == 'cover' else config.image.body_output_ceiling
            if stopped:
                record.update(status='blocked', reason='stopped_before_call')
                continue
            stopped = not _generate(ctx, provider, check, record, prompts[role], f'{run_id}-v{number}-{role}', cap)
            LOG.info('image-compare variant=%s@%s role=%s status=%s usd=%.6f', model, size, role, record['status'],
                     record['usd'])
            _write_board(ctx, rows, False)
    ctx['config'] = config
    return _write_board(ctx, rows, True)
