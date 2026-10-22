"""Default-off, exact-human-approved immediate publication for one staging TEST.

The existing approval TEXT holds the audit; neither a post's immutable slot nor
the reviewed Draft payload is changed. This module grants no recovery write.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from .publication import (AdminError, approval_fingerprint, binding, digest,
                          row_fingerprint, STAGING_SITE_ORIGIN,
                          STAGING_API_ORIGIN, STAGING_MEDIA_ORIGIN)

ROOT = Path('/var/lib/agentfleet/nailblog-staging-20261021-r11')
RUN = 'staging-soak-20261021-r11-immediate'
KIND = 'nailblog_staging_immediate_approval_v1'
_CURRENT = ContextVar('nailblog_staging_immediate', default=None)
_HEX = re.compile(r'[0-9a-f]{64}')


def _need(value):
    if not value:
        raise AdminError('staging_immediate_binding_invalid')


def _time(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        _need(result.utcoffset() is not None)
        return result.timestamp()
    except (AttributeError, TypeError, ValueError):
        raise AdminError('staging_immediate_timestamp_invalid') from None


def _path(path):
    path = Path(path)
    _need(path.is_absolute() and '..' not in path.parts and path.is_relative_to(ROOT))
    _need(path.resolve().is_relative_to(ROOT.resolve()))
    _need(not any(p.is_symlink() for p in (path, *path.parents)))
    return path


def _file(path, expected=None, *, root_owned=False):
    path = _path(path)
    _need(path.is_file())
    info = path.stat()
    _need(info.st_size <= 2 * 1024 * 1024)
    _need(_file_owner(info, root_owned))
    raw = path.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    if expected is not None:
        _need(isinstance(expected, str) and _HEX.fullmatch(expected) and actual == expected)
    try:
        return json.loads(raw), actual
    except (ValueError, UnicodeError):
        raise AdminError('staging_immediate_receipt_invalid') from None


def _file_owner(info, root_owned):
    return os.name != 'posix' or (info.st_uid == (0 if root_owned else 992) and
        stat.S_IMODE(info.st_mode) in ((0o600, 0o640) if root_owned else (0o600,)))


@dataclass(frozen=True)
class _Grant:
    policy_path: str
    policy_sha: str
    control_path: str
    control_sha: str
    decision_path: str
    decision_sha: str
    run_id: str
    snapshot: str


def _configuration(config, state, grant, now):
    _need(config.mode == 'live' and config.contract.verified is True)
    _need(config.contract.environment == 'staging')
    _need((config.contract.site_origin, config.contract.api_origin,
           config.contract.media_origin) ==
          (STAGING_SITE_ORIGIN, STAGING_API_ORIGIN, STAGING_MEDIA_ORIGIN))
    _need(Path(config.db_path) == ROOT / 'nailblog.sqlite3' and state.path == config.db_path)
    from .admin_images import active_test_image_v2
    waiver=active_test_image_v2(config)
    decision_dir=ROOT/'telegram-owner-decisions'
    if waiver is not None:
        _need(grant.control_sha==waiver['grant']['control_sha256'] and grant.control_path==waiver['grant']['control_path'])
        decision_dir=ROOT/'continuation-image-waiver-v2'/'telegram-owner-decisions'
    _need(grant.decision_path == str(decision_dir / (RUN + '-approve_draft.json')))
    policy, _ = _file(grant.policy_path, grant.policy_sha, root_owned=True)
    control, _ = _file(grant.control_path, grant.control_sha, root_owned=True)
    _need(policy.get('kind') == 'nailblog_staging_immediate_policy_v1')
    _need(policy.get('owner') == 'AlexChu' and policy.get('environment') == 'staging')
    _need(policy.get('root') == str(ROOT) and policy.get('db_path') == config.db_path)
    _need(policy.get('run_id') == grant.run_id == RUN)
    _need(policy.get('site_origin') == config.contract.site_origin and
          policy.get('api_origin') == config.contract.api_origin and
          policy.get('media_origin') == config.contract.media_origin)
    _need(policy.get('account_id') == config.cloudflare.account_id)
    _need(policy.get('dispatch_window_seconds') == 900 and
          policy.get('new_images_only') is True and policy.get('no_auto_approval') is True)
    _need('Đăng ngay sau khi tôi duyệt đủ' in policy.get('owner_quote', ''))
    created, expires = _time(policy.get('created_at_utc')), _time(policy.get('expires_at_utc'))
    _need(created <= now < expires and 0 < expires - created <= 86400)
    _need(control.get('root') == str(ROOT) and control.get('environment') == 'staging' and
          control.get('owner') == 'AlexChu' and control.get('publication_mode') == 'immediate_after_approval')
    _need(control.get('immediate_policy_sha256') == grant.policy_sha and
          control.get('immediate_policy_path') == grant.policy_path)
    _need([r.get('run_id') for r in control.get('runs', [])] == [RUN])
    _need(control.get('controller_sha256') == policy.get('controller_sha256'))
    _need(_HEX.fullmatch(policy.get('controller_sha256', '')))
    controller = _path(control.get('controller_path', ''))
    _need(controller.is_file() and hashlib.sha256(controller.read_bytes()).hexdigest() == policy['controller_sha256'])
    _need(_file_owner(controller.stat(), True))
    toml = _path(control.get('nailblog_config', ''))
    _need(toml == ROOT / 'nailblog.toml' and
          hashlib.sha256(toml.read_bytes()).hexdigest() == policy.get('toml_sha256') == control.get('nailblog_config_sha256'))
    _need(not any(p.is_symlink() for p in (toml, *toml.parents)) and _file_owner(toml.stat(), True))
    from .config import load_config
    _need(load_config(str(toml)) == config)
    _runtime(control.get('runtime_hashes'))
    return policy, control


def _runtime(expected):
    package = Path(__file__).parent
    actual = {str(p.relative_to(package)).replace('\\', '/')
              for p in package.rglob('*.py')}
    actual.add('images/templates/cover.html')
    _need(isinstance(expected, dict) and set(expected) == actual and len(actual) == 40)
    for name, pin in expected.items():
        relative = Path(name)
        _need(not relative.is_absolute() and '..' not in relative.parts and
              isinstance(pin, str) and _HEX.fullmatch(pin))
        path = package / relative
        _need(path.is_file() and not any(p.is_symlink() for p in (path, *path.parents)))
        _need(hashlib.sha256(path.read_bytes()).hexdigest() == pin)


@contextmanager
def staging_immediate_publish(config, state, *, policy_path, policy_sha256,
                              control_path, control_sha256, decision_path,
                              decision_sha256, run_id, snapshot, now=None):
    """Enter only after a real native exact-Draft approval; validates again per write."""
    grant = _Grant(str(policy_path), policy_sha256, str(control_path), control_sha256,
                   str(decision_path), decision_sha256, run_id, snapshot)
    _configuration(config, state, grant, datetime.now(timezone.utc).timestamp() if now is None else now)
    token = _CURRENT.set(grant)
    try:
        yield
    finally:
        _CURRENT.reset(token)


def _review(run, row, config, phase):
    saved = json.loads(run['document'])
    value = {'run_id': run['run_id'], 'post_id': run['post_id'], 'phase': phase,
             'environment': binding(config), 'article': saved.get('article'),
             'quality': saved.get('quality'), 'image_hashes': saved.get('image_hashes'),
             'images': saved.get('image_metadata'), 'image_quota_forecast': saved.get('image_quota_forecast')}
    if saved.get('test_image_waiver'):
        value.update(test_image_waiver=saved['test_image_waiver'],automatic_flow_verdict='HELD',model_quality_verdict='FAIL')
    if saved.get('review_bypass'):
        value.update(review_bypass=saved['review_bypass'],automatic_flow_verdict='REVIEW_BYPASSED')
    value.update(**{k: saved.get(k) for k in ('facts', 'words', 'repaired', 'writer_audit', 'text_dispatches', 'artifact_status')})
    if phase == 'draft_verified':
        value.update(draft_fingerprint=row_fingerprint(row, config), admin_id=row['admin_id'])
    return digest(value)


def _human_decision(path, expected_sha, action, snapshot, journal, native, now):
    decision, actual_sha = _file(path, expected_sha)
    _need(decision.get('kind') == 'nailblog_soak_owner_decision_v1' and
          decision.get('owner') == 'AlexChu' and decision.get('environment') == 'staging' and
          decision.get('site_origin') == STAGING_SITE_ORIGIN and
          decision.get('run_id') == RUN and decision.get('action') == action and decision.get('snapshot') == snapshot)
    telegram = decision.get('telegram', {})
    owner = native.get('owner_id')
    _need(type(owner) is int and owner > 0 and telegram.get('sender_id') == telegram.get('peer_id') == owner)
    _need(telegram.get('automation_account_id') == native.get('automation_id') != owner and
          telegram.get('verified_private_human_incoming') is True)
    message = telegram.get('message_id')
    _need(type(message) is int and message > 0)
    command = 'accept-images' if action == 'accept_images' else 'approve-draft'
    quote = '/nailblog-test ' + command + ' ' + RUN + ' ' + snapshot
    _need(decision.get('owner_quote') == quote and
          telegram.get('original_text_sha256') == hashlib.sha256(quote.encode()).hexdigest())
    accepted = journal.get('accepted', {}).get(str(owner) + ':' + str(message), {})
    _need(accepted.get('decision_sha256') == actual_sha and accepted.get('run_id') == RUN and
          accepted.get('snapshot') == snapshot and accepted.get('action') == action)
    notice = journal.get('notices', {}).get('review:' + RUN + ':' + snapshot, {})
    ids = notice.get('message_ids')
    _need(notice.get('complete') is True and isinstance(ids, list) and ids and
          all(type(v) is int and v > 0 for v in ids) and telegram.get('review_request_message_ids') == ids)
    approved_at = _time(decision.get('created_at_utc'))
    _need(_time(notice.get('sent_at_utc')) <= approved_at <= now < _time(decision.get('expires_at')))
    return actual_sha, approved_at


def _audit(config, state, row, op, now, db):
    grant = _CURRENT.get()
    _need(grant is not None)
    policy, control = _configuration(config, state, grant, now)
    run = db.execute('SELECT * FROM onepost_runs WHERE run_id=?', (RUN,)).fetchone()
    _need(run is not None and run['post_id'] == row['id'] and run['phase'] in ('draft_verified', 'scheduled'))
    saved = json.loads(run['document'])
    document = json.loads(row['draft_json'])
    from .admin_images import active_test_image_v2, verify_test_operation
    waiver=active_test_image_v2(config)
    if saved.get('test_image_waiver') or document.get('test_image_waiver'):
        _need(waiver is not None and saved.get('test_image_waiver')==document.get('test_image_waiver'))
        verify_test_operation(document,config,run_id=RUN,state=state,post_id=row['id'])
    else:_need(waiver is None)
    hashes = saved.get('image_hashes')
    _need(isinstance(hashes, list) and len(hashes) == 4 and len(set(hashes)) == 4 and
          all(isinstance(h, str) and _HEX.fullmatch(h) for h in hashes))
    _need(document.get('image_hashes') == hashes and
          all(document.get(k) == v for k, v in saved['article'].items()))
    _need(control.get('prior_image_inventory_verified') is True)
    prior = control.get('prior_images')
    _need(isinstance(prior, list) and all(isinstance(v, dict) and _HEX.fullmatch(v.get('sha256', '')) for v in prior))
    _need(not set(hashes) & {v['sha256'] for v in prior})
    _need(run['image_approval'] == digest({'article': saved['article'], 'hashes': hashes}))
    from .onepost import OnePost
    OnePost.verify_images(saved, config=config)
    draft_snapshot = _review(run, row, config, 'draft_verified')
    _need(grant.snapshot == draft_snapshot)
    native_root=ROOT/'continuation-image-waiver-v2' if waiver is not None else ROOT
    if waiver is not None:
        _need(control.get('telegram_owner_journal_path')==str(native_root/'telegram-owner-journal.json') and
              control.get('native_transport_readiness_path')==str(native_root/'native-transport-readiness.json') and
              control.get('telegram_owner_decisions_dir')==str(native_root/'telegram-owner-decisions'))
    journal, _ = _file(native_root / 'telegram-owner-journal.json')
    native, _ = _file(native_root / 'native-transport-readiness.json')
    _need(all(native.get(k) is True for k in ('ready', 'human_owner_verified', 'human_automation', 'connected', 'authorized')) and
          native.get('config_sha256') == grant.control_sha and
          native.get('controller_sha256') == policy['controller_sha256'])
    _need(type(native.get('automation_id')) is int and type(native.get('owner_id')) is int and
          native['automation_id'] > 0 and native['owner_id'] > 0 and native['automation_id'] != native['owner_id'])
    if config.owner_ids:
        _need(tuple(config.owner_ids) == (native['owner_id'],))
    image_sha, image_at = _human_decision(native_root / 'telegram-owner-decisions' / (RUN + '-accept_images.json'), None,
                                         'accept_images', _review(run, row, config, 'images_ready'), journal, native, now)
    draft_sha, approved_at = _human_decision(grant.decision_path, grant.decision_sha, 'approve_draft',
                                           draft_snapshot, journal, native, now)
    _need(image_at <= approved_at <= now <= approved_at + 900)
    _need(_time(policy['created_at_utc']) <= image_at and approved_at < _time(policy['expires_at_utc']))
    return {'run_id': RUN, 'post_id': row['id'], 'admin_id': row['admin_id'], 'environment': binding(config),
            'db_path': state.path, 'policy_sha256': grant.policy_sha, 'control_sha256': grant.control_sha,
            'controller_sha256': policy['controller_sha256'], 'draft_snapshot': draft_snapshot,
            'draft_fingerprint': row_fingerprint(row, config), 'payload_digest': digest(json.loads(op['payload'])),
            'receipt_digest': digest(json.loads(op['receipt'])), 'image_decision_sha256': image_sha,
            'draft_decision_sha256': draft_sha, 'owner_approved_at': approved_at, 'window_seconds': 900}


_AUDIT_KEYS = frozenset(('run_id', 'post_id', 'admin_id', 'environment', 'db_path', 'policy_sha256',
                         'control_sha256', 'controller_sha256', 'draft_snapshot', 'draft_fingerprint',
                         'payload_digest', 'receipt_digest', 'image_decision_sha256',
                         'draft_decision_sha256', 'owner_approved_at', 'window_seconds'))


def approval_audit(value, expected_fingerprint):
    """Parse strict envelope or unchanged legacy fingerprint. Never grants a write."""
    if isinstance(value, str) and _HEX.fullmatch(value):
        if value != expected_fingerprint:
            raise AdminError('approval_changed')
        return None
    try:
        envelope = json.loads(value)
    except (TypeError, ValueError):
        raise AdminError('staging_immediate_approval_invalid') from None
    _need(isinstance(envelope, dict) and set(envelope) == {'kind', 'fingerprint', 'audit'})
    _need(envelope['kind'] == KIND and envelope['fingerprint'] == expected_fingerprint)
    audit = envelope['audit']
    _need(isinstance(audit, dict) and set(audit) == _AUDIT_KEYS)
    _need(audit.get('run_id') == RUN and audit.get('db_path') == str(ROOT / 'nailblog.sqlite3'))
    _need(type(audit.get('post_id')) is int and audit['post_id'] > 0 and bool(audit.get('admin_id')))
    _need(type(audit.get('owner_approved_at')) in (float, int) and audit.get('window_seconds') == 900)
    _need(audit.get('environment', {}).get('environment') == 'staging')
    for key in _AUDIT_KEYS:
        if key.endswith(('sha256', 'snapshot', 'fingerprint', 'digest')):
            _need(isinstance(audit[key], str) and _HEX.fullmatch(audit[key]))
    return audit


def bound_approval_audit(config, state, row, op):
    audit = approval_audit(op['approval'], approval_fingerprint(row, op, config))
    if audit is not None:
        _need(config.contract.environment == 'staging' and state.path == audit['db_path'])
        _need(audit['environment'] == binding(config) and audit['post_id'] == row['id'] and
              audit['admin_id'] == row['admin_id'] and
              audit['draft_fingerprint'] == row_fingerprint(row, config) and
              audit['payload_digest'] == digest(json.loads(op['payload'])) and
              audit['receipt_digest'] == digest(json.loads(op['receipt'])))
    return audit


def immediate_approval(config, state, row, op, now, db):
    if _CURRENT.get() is None:
        return None
    audit = _audit(config, state, row, op, now, db)
    return json.dumps({'kind': KIND, 'fingerprint': approval_fingerprint(row, op, config), 'audit': audit}, sort_keys=True)


def immediate_publish_allowed(config, state, row, op, now, db):
    audit = bound_approval_audit(config, state, row, op)
    if audit is None:
        return False
    _need(_CURRENT.get() is not None)
    _need(audit == _audit(config, state, row, op, now, db))
    return True


def explicit_publish_candidate(state, config, post_id, now):
    """Only a requested, audited post can bypass normal due-candidate selection."""
    if _CURRENT.get() is None:
        return None
    with state.connection() as db:
        row = db.execute('SELECT * FROM posts WHERE id=?', (post_id,)).fetchone()
        op = db.execute('SELECT * FROM admin_operations WHERE post_id=?', (post_id,)).fetchone()
        _need(row is not None and op is not None and row['state'] == 'scheduled')
        _need(immediate_publish_allowed(config, state, row, op, now, db))
        return dict(row)
