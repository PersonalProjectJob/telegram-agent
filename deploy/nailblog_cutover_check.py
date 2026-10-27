"""READ-ONLY NailBlog cutover inventory. Run on the host as root; prints one JSON document.

Never writes, restarts, enables or masks anything, and never prints a secret: only booleans for the Gemini key,
whitelisted fields for markers/receipts, and the sha256 of the retired ledger dump (taken from a private copy).
"""
import argparse
import contextlib
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path

try:
    import pwd
except ImportError:   # Windows: fixtures only
    pwd = None

CATEGORIES = ('hot-trend', 'nail-job', 'nail-salon', 'did-you-know')
PLACEHOLDER = re.compile(r'^a1b2c3d4-e5f6-7890-abcd-ef12345678[0-9a-f]{2}$', re.I)
MISSING_CLAIMS_SQL = ('SELECT r.run_id, r.phase, r.post_id, r.document FROM onepost_runs r '
                      'LEFT JOIN onepost_topic_claims c ON c.run_id = r.run_id WHERE c.run_id IS NULL ORDER BY r.run_id')
GEMINI_KEY = 'NAILBLOG_GEMINI_IMAGE_KEY'
DEFAULTS = {
    'unit_dir': '/etc/systemd/system',
    'transient_unit_dir': '/run/systemd/transient',
    'lib_unit_dir': '/lib/systemd/system',
    'db': '/var/lib/agentfleet/nailblog/nailblog.sqlite3',
    'ledger': '/var/lib/agentfleet/nailblog/ledger/nailblog-ledger.sqlite3',
    'queue': '/var/lib/nailblog-subscription/queue',
    'private_dir': '/home/startechai/snap/antigravity-cli/common/nailblog-worker',
    'config': '/etc/nailblog/nailblog.toml',
    'env_file': '/etc/agentfleet/tg-nailblog.env',
    'lock': '/run/agentfleet/ollama.lock',
    'proc_root': '/proc',
}


def run_systemctl(args):
    """Return stdout or a structured query error. Read-only subcommands only; never echo stderr/argv."""
    if args[0] not in ('is-enabled', 'is-active', 'list-units', 'list-timers'):
        raise ValueError('only read-only systemctl subcommands are allowed')
    try:
        done = subprocess.run(['systemctl', *args], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as error:
        return {'error': type(error).__name__}
    if done.returncode != 0 and (args[0].startswith('list-') or not done.stdout.strip()):
        return {'error': 'command_failed', 'returncode': done.returncode}
    return done.stdout.strip()


def read_text(path):
    try:
        return Path(path).read_text(encoding='utf-8', errors='replace')
    except OSError:
        return None


def exec_start_lines(text):
    return [line.strip() for line in (text or '').splitlines() if line.strip().startswith('ExecStart=')]


def inspect_unit_file(path):
    path = Path(path)
    masked = path.is_symlink() and os.readlink(path) == '/dev/null'
    text = None if masked else read_text(path)
    lines = exec_start_lines(text)
    return {'path': str(path), 'masked': masked,
            'exec_start_sha256': [hashlib.sha256(line.encode('utf-8')).hexdigest() for line in lines],
            'report_bridge': any('report_bridge' in line for line in lines)}


LEGACY_UNIT = re.compile(r'^nailblog-(?:daily4|makeup|staging-)')
UNIT_NAME = re.compile(r'\b([A-Za-z0-9_.@:-]*nailblog[A-Za-z0-9_.@:-]*\.(?:service|timer))\b')
UNIT_QUERY_STATES = {
    'is-active': {'active', 'inactive', 'failed', 'activating', 'deactivating', 'reloading', 'maintenance', 'refreshing'},
    'is-enabled': {'enabled', 'enabled-runtime', 'linked', 'linked-runtime', 'alias', 'masked', 'masked-runtime',
                   'static', 'indirect', 'disabled', 'generated', 'transient', 'not-found'},
}


def inventory_units(unit_dir, systemctl, extra_dirs=()):
    files, dropins = {}, []
    query_errors = []

    def query(args):
        result = systemctl(args) if systemctl else None
        error = {'query': args[0]}
        if args[0] in UNIT_QUERY_STATES:
            error['unit'] = args[1]
        if isinstance(result, dict) and result.get('error'):
            error.update({k: result[k] for k in ('error', 'returncode') if k in result})
        elif result is None:
            error['error'] = 'unavailable'
        elif not isinstance(result, str) or (args[0] in UNIT_QUERY_STATES and result not in UNIT_QUERY_STATES[args[0]]):
            error['error'] = 'invalid_result'
            if isinstance(result, str):
                error['output_sha256'] = hashlib.sha256(result.encode()).hexdigest()
        else:
            return result
        query_errors.append(error)
        return None

    for directory in (unit_dir, *extra_dirs):
        root = Path(directory)
        if not root.is_dir():
            continue
        for path in sorted(root.iterdir()):
            if 'nailblog' not in path.name:
                continue
            if path.name.endswith('.d') and path.is_dir():
                for conf in sorted(path.glob('*.conf')):
                    dropins.append({'unit': path.name[:-2], **inspect_unit_file(conf)})
            elif path.name.endswith(('.timer', '.service')):
                files.setdefault(path.name, []).append(inspect_unit_file(path))
        for path in sorted(root.glob('*.d/*.conf')):   # drop-ins of units whose own name lacks "nailblog"
            owner = path.parent.name[:-2]
            if 'nailblog' not in owner and 'nailblog' in (read_text(path) or ''):
                dropins.append({'unit': owner, **inspect_unit_file(path)})
    raw = query(['list-units', '--all', '--plain', '--no-legend', '*nailblog*'])
    timer_raw = query(['list-timers', '--all', '--plain', '--no-legend'])
    runtime_names = set(UNIT_NAME.findall(raw or ''))
    timer_names = set(UNIT_NAME.findall(timer_raw or ''))
    records = []
    for name in sorted(set(files) | runtime_names | timer_names):
        candidates = files.get(name, [])
        item = {'name': name, 'path': candidates[0]['path'] if candidates else None,
                'paths': [c['path'] for c in candidates],
                'exec_start_sha256': candidates[0]['exec_start_sha256'] if candidates else [],
                'report_bridge': any(c['report_bridge'] for c in candidates),
                'enabled': query(['is-enabled', name]),
                'active': query(['is-active', name])}
        item['masked'] = item['enabled'] in ('masked', 'masked-runtime')
        item['persistent_masked'] = item['enabled'] == 'masked'
        item['transient'] = item['enabled'] == 'transient' or bool(extra_dirs and any(
            Path(c['path']).parent == Path(extra_dirs[0]) for c in candidates))
        records.append(item)
    units = [i for i in records if i['name'].endswith('.service')]
    timers = [i for i in records if i['name'].endswith('.timer')]
    bad = []
    if raw is None or timer_raw is None:
        bad.append('runtime_inventory_unavailable')
    for error in query_errors:
        bad.append('query_error:' + error['query'] + (':' + error['unit'] if 'unit' in error else ''))
    for item in records:
        if LEGACY_UNIT.match(item['name']):
            if item['active'] != 'inactive':
                bad.append('legacy_not_inactive:' + item['name'])
            removed = not item['paths'] and item['name'] not in runtime_names and item['enabled'] == 'not-found'
            if not removed and not item['persistent_masked']:
                bad.append('legacy_not_persistently_masked:' + item['name'])
            if item['transient']:
                bad.append('legacy_transient_still_present:' + item['name'])
    # No independent NailBlog timer may remain in the runtime timer list, even if its file is masked.
    for name in sorted(n for n in timer_names if n.endswith('.timer')):
        bad.append('runtime_timer_present:' + name)
    return {'units': units, 'dropins': dropins, 'timers': timers, 'query_errors': query_errors,
            'runtime_timer_names': sorted(n for n in timer_names if n.endswith('.timer')),
            'failed': [i['name'] for i in records if i['active'] == 'failed'],
            'legacy_gate': {'pass': not bad, 'reasons': bad},
            'report_bridge_present': any(i['report_bridge'] for i in records + dropins)}


def connect_ro(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)


@contextlib.contextmanager
def private_copy(path):
    """Read-only connection on a copy (db + -wal, never -shm) in a 0700 temp dir; the original is only read."""
    source = Path(path)
    with tempfile.TemporaryDirectory(prefix='nailblog-cutover-') as tmp:
        os.chmod(tmp, 0o700)
        target = Path(tmp) / source.name
        shutil.copyfile(source, target)
        wal = Path(str(source) + '-wal')
        if wal.is_file():
            shutil.copyfile(wal, str(target) + '-wal')
        db = connect_ro(target)
        try:
            yield db
        finally:
            db.close()


def tables(db):
    return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def inspect_db(path):
    out = {'path': str(path), 'exists': Path(path).is_file(), 'control': None, 'onepost_missing_claims': None}
    if not out['exists']:
        return out
    try:
        with private_copy(path) as db:
            db.row_factory = sqlite3.Row
            names = tables(db)
            if 'control' in names:
                columns = {row['name'] for row in db.execute('PRAGMA table_info(control)')}
                out['control'] = []
                for row in db.execute('SELECT * FROM control'):
                    row = dict(row)
                    item = {key: row[key] for key in ('paused', 'image_mode', 'last_result') if key in row}
                    if 'armed_binding' in columns:
                        item['armed'] = row['armed_binding'] != ''   # the binding itself is never printed
                    out['control'].append(item)
            if 'onepost_runs' in names:
                if 'onepost_topic_claims' in names:
                    rows = db.execute(MISSING_CLAIMS_SQL).fetchall()
                else:
                    rows = db.execute('SELECT run_id, phase, post_id, document FROM onepost_runs ORDER BY run_id').fetchall()
                missing = []
                for row in rows:
                    try:
                        topic = json.loads(row['document']).get('topic_id')
                    except (TypeError, ValueError, AttributeError):
                        topic = None
                    missing.append({'run_id': row['run_id'], 'phase': row['phase'], 'post_id': row['post_id'], 'topic_id': topic})
                out['onepost_missing_claims'] = missing
                out['onepost_claims_table'] = 'onepost_topic_claims' in names
                by_topic = {}
                for row in db.execute('SELECT run_id, document FROM onepost_runs ORDER BY run_id'):
                    try:
                        topic = json.loads(row['document']).get('topic_id')
                    except (TypeError, ValueError, AttributeError):
                        topic = None
                    if type(topic) in (int, str):
                        by_topic.setdefault(topic, []).append(row['run_id'])
                out['legacy_topic_claim_conflict'] = [
                    {'topic_id': topic, 'run_ids': runs} for topic, runs in by_topic.items() if len(runs) > 1]
            if 'admin_binding' in names:
                out['admin_binding'] = [
                    {'id': row['id'], 'sha256': hashlib.sha256(row['value'].encode('utf-8')).hexdigest()}
                    for row in db.execute('SELECT id, value FROM admin_binding ORDER BY id')]
            out['unfinished_posts'] = []
            if 'posts' in names:
                for row in db.execute("SELECT id, topic_id, state, slot_at FROM posts WHERE state NOT IN ('published','held') ORDER BY id"):
                    item = dict(row)
                    item['autoflow_member'] = bool('autoflow_runs' in names and db.execute(
                        'SELECT 1 FROM autoflow_runs WHERE topic_id=?', (row['topic_id'],)).fetchone())
                    out['unfinished_posts'].append(item)
            out['approved_unpublished_admin_operations'] = []
            if 'admin_operations' in names:
                for row in db.execute("SELECT post_id FROM admin_operations WHERE approval != '' AND publish_day = '' ORDER BY post_id"):
                    item = {'post_id': row['post_id']}
                    post = db.execute('SELECT state, slot_at FROM posts WHERE id=?', (row['post_id'],)).fetchone() if 'posts' in names else None
                    if post:
                        item.update(dict(post))
                    out['approved_unpublished_admin_operations'].append(item)
    except (sqlite3.Error, OSError) as error:
        out['error'] = str(error)
    return out


def ledger_digest(path):
    """sha256 of a canonical dump of paid_intents, read from a private copy so the ledger itself is never opened."""
    out = {'path': str(path), 'exists': Path(path).is_file(), 'paid_intents_rows': None, 'paid_intents_sha256': None}
    if not out['exists']:
        return out
    try:
        with private_copy(path) as db:
            if 'paid_intents' in tables(db):
                cursor = db.execute('SELECT * FROM paid_intents ORDER BY 1')
                columns = [c[0] for c in cursor.description]
                rows = [list(map(_plain, row)) for row in cursor.fetchall()]
                dump = json.dumps({'columns': columns, 'rows': rows}, sort_keys=True, separators=(',', ':'))
                out['paid_intents_rows'] = len(rows)
                out['paid_intents_sha256'] = hashlib.sha256(dump.encode('utf-8')).hexdigest()
    except (sqlite3.Error, OSError) as error:
        out['error'] = str(error)
    return out


def _plain(value):
    return value.hex() if isinstance(value, bytes) else value


def read_json_fields(path, keys):
    text = read_text(path)
    if text is None:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return {'unreadable': True}
    return {key: data[key] for key in keys if isinstance(data, dict) and key in data}


def inspect_queue(queue, private_dir):
    queue, private_dir = Path(queue), Path(private_dir)
    out = {'path': str(queue), 'exists': queue.is_dir(), 'jobs': []}
    if not out['exists']:
        return out
    for job in sorted(p for p in queue.iterdir() if p.is_dir()):
        marker = private_dir / (job.name + '.json')
        receipt = job / 'receipt.json'
        request = read_json_fields(job / 'request.json', ('expires_at', 'kind', 'protocol'))
        protocol_kind = {'nailblog-subscription-v1': 'image', 'nailblog-agy-text-v1': 'text'}
        out['jobs'].append({
            'job': job.name,
            'files': sorted(p.name for p in job.iterdir()),
            'request': request,
            'kind': (request or {}).get('kind') or protocol_kind.get((request or {}).get('protocol')),
            'expires_at': (request or {}).get('expires_at'),
            'marker': {'path': str(marker), 'exists': marker.is_file(),
                       'fields': read_json_fields(marker, ('day', 'kind', 'run_id'))},
            'receipt': {'exists': receipt.is_file(),
                        'fields': read_json_fields(receipt, ('status', 'protocol', 'job_id', 'kind'))}})
    ready = queue / 'worker-ready.json'
    out['worker_ready'] = read_json_fields(ready, ('protocol', 'at', 'busy_job')) if ready.is_file() else None
    return out


def user_name(uid):
    try:
        return pwd.getpwuid(uid).pw_name
    except (AttributeError, KeyError, ValueError):
        return str(uid)


def read_bytes(path):
    try:
        return Path(path).read_bytes()
    except OSError:
        return None


def inspect_processes(proc_root, lock):
    """agy processes (pid, user, etime) and holders of the model lock. Never prints argv: prompts are in it."""
    root = Path(proc_root)
    out = {'agy': [], 'lock_holders': [], 'lock': str(lock)}
    if not root.is_dir():
        return out
    uptime_text = read_text(root / 'uptime')
    uptime = float(uptime_text.split()[0]) if uptime_text else None
    try:
        hertz = os.sysconf('SC_CLK_TCK')
    except (AttributeError, ValueError, OSError):
        hertz = 100
    for entry in sorted((p for p in root.iterdir() if p.name.isdigit()), key=lambda p: int(p.name)):
        argv = (read_bytes(entry / 'cmdline') or b'').split(b'\0')
        first = argv[0].decode('utf-8', 'replace') if argv else ''
        comm = (read_text(entry / 'comm') or '').strip()
        try:
            uid = entry.stat().st_uid
        except OSError:
            uid = None
        if Path(first).name == 'agy' or comm == 'agy':
            etime = None
            stat = read_text(entry / 'stat')
            if stat and uptime is not None:
                try:
                    start = int(stat.rsplit(')', 1)[1].split()[19])
                    etime = int(uptime - start / hertz)
                except (IndexError, ValueError):
                    pass
            out['agy'].append({'pid': int(entry.name), 'user': user_name(uid) if uid is not None else None,
                               'etime_seconds': etime})
        try:
            for fd in (entry / 'fd').iterdir():
                if os.readlink(fd) == str(lock):
                    out['lock_holders'].append({'pid': int(entry.name), 'comm': comm,
                                                'user': user_name(uid) if uid is not None else None})
                    break
        except OSError:
            continue
    return out


def verify_probe_categories(data, ids, report_path):
    """Compare against fresh authenticated options; UUID patterns are not evidence of placeholders."""
    out = {'path': str(report_path) if report_path else None, 'pass': False}
    if report_path is None:
        out['error'] = 'fresh_admin_probe_required'
        return out
    try:
        raw = Path(report_path).read_bytes()
        report = json.loads(raw)
        age = time.time() - Path(report_path).stat().st_mtime
        if not isinstance(report, dict):
            raise ValueError()
        out['report_sha256'] = hashlib.sha256(raw).hexdigest()
        if not -300 <= age <= 86400:
            out['error'] = 'stale_admin_probe'
            return out
        if (report.get('authenticated') is not True or report.get('writes_performed') is not False
                or report.get('editor_roundtrip') is not True):
            out['error'] = 'authenticated_readonly_probe_required'
            return out
        contract = data.get('contract') or {}
        keys = ('environment', 'site_origin', 'api_origin') + (('media_origin',) if contract.get('media_origin') else ())
        if any(not contract.get(key) or report.get(key) != contract[key] for key in keys):
            out['error'] = 'probe_environment_mismatch'
            return out
        categories = report.get('categories')
        if not isinstance(categories, list):
            raise ValueError()
        observed = {}
        for category in categories:
            if not isinstance(category, dict):
                raise ValueError()
            slug = re.sub(r'[^a-z0-9]+', '-', str(category.get('label', '')).casefold()).strip('-')
            if slug in CATEGORIES:
                if slug in observed:
                    out['error'] = 'probe_category_ambiguous'
                    return out
                observed[slug] = category.get('id')
        mismatched = [slug for slug in CATEGORIES if not ids.get(slug) or observed.get(slug) != ids.get(slug)]
        out['mismatched_slugs'] = mismatched
        out['pass'] = not mismatched and len(set(observed.values())) == len(CATEGORIES)
        if not out['pass']:
            out['error'] = 'probe_category_mismatch'
    except (OSError, ValueError, TypeError):
        out['error'] = 'invalid_admin_probe'
    return out


def inspect_config(path, probe_report=None):
    out = {'path': str(path), 'exists': Path(path).is_file(), 'category_ids': None}
    if not out['exists']:
        return out
    try:
        data = tomllib.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        out['error'] = type(error).__name__
        return out
    ids = (data.get('admin') or {}).get('category_ids') or {}
    out['category_ids'] = {
        'slugs_present': sorted(ids), 'slugs_missing': [c for c in CATEGORIES if c not in ids],
        'pattern_suspect': sorted(slug for slug, guid in ids.items() if PLACEHOLDER.match(str(guid)))}
    out['category_verification'] = verify_probe_categories(data, ids, probe_report)
    out['slots'] = len((data.get('service') or {}).get('slots') or [])
    return out


def inspect_env(path):
    text = read_text(path)
    if text is None:
        return {'path': str(path), 'exists': False, 'gemini_image_key_present': None}
    present = any(line.strip().split('=', 1)[0].removeprefix('export ').strip() == GEMINI_KEY
                  for line in text.splitlines() if line.strip() and not line.lstrip().startswith('#'))
    return {'path': str(path), 'exists': True, 'gemini_image_key_present': present}


def inspect_listing_sample(path):
    if path is None:
        return None
    out = {'path': str(path), 'exists': Path(path).is_file(), 'object_keys': []}
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        out['error'] = 'unreadable_json'
        return out
    if not isinstance(data, dict):
        out['error'] = 'listing_not_object'
        return out
    # Schema only. Do not print article values, credentials or arbitrary item payloads.
    out['object_keys'].append({'path': '$', 'keys': sorted(data)})
    for key, value in sorted(data.items()):
        if isinstance(value, dict):
            out['object_keys'].append({'path': '$.' + key, 'keys': sorted(value)})
    return out


def collect(args, systemctl=run_systemctl):
    return {
        'systemd': inventory_units(args.unit_dir, None if args.no_systemctl else systemctl,
                                  (args.transient_unit_dir, args.lib_unit_dir)),
        'database': inspect_db(args.db),
        'queue': inspect_queue(args.queue, args.private_dir),
        'ledger': ledger_digest(args.ledger),
        'processes': inspect_processes(args.proc_root, args.lock),
        'config': inspect_config(args.config, args.admin_probe_report),
        'env': inspect_env(args.env_file),
        'admin_listing_sample': inspect_listing_sample(args.admin_listing_sample),
    }


def parse(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    for name, default in DEFAULTS.items():
        parser.add_argument('--' + name.replace('_', '-'), default=default)
    parser.add_argument('--no-systemctl', action='store_true', help='skip enabled/active/failed (fixtures, non-systemd hosts)')
    parser.add_argument('--admin-listing-sample', help='saved admin listing JSON; reports key names only, never values')
    parser.add_argument('--admin-probe-report', help='fresh authenticated readonly probe report; verifies actual category IDs')
    return parser.parse_args(argv)


def main(argv=None, systemctl=run_systemctl):
    print(json.dumps(collect(parse(argv), systemctl), indent=2, sort_keys=True, default=str))
    return 0


if __name__ == '__main__':
    sys.exit(main())
