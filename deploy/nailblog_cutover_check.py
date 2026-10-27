"""READ-ONLY NailBlog cutover inventory. Run on the host as root; prints one JSON document.

Never writes, restarts, enables or masks anything, and never prints a secret: only booleans for the Gemini key,
whitelisted fields for markers/receipts, and the sha256 of the retired ledger dump (taken from a private copy).
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
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
    """Return stdout of `systemctl <args>` or None when systemctl is unavailable. Read-only subcommands only."""
    if args[0] not in ('is-enabled', 'is-active', 'list-units'):
        raise ValueError('only read-only systemctl subcommands are allowed')
    try:
        done = subprocess.run(['systemctl', *args], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
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
    return {'path': str(path), 'masked': masked, 'exec_start': lines,
            'report_bridge': any('report_bridge' in line for line in lines)}


def inventory_units(unit_dir, systemctl):
    unit_dir = Path(unit_dir)
    units, dropins, timers = [], [], []
    if unit_dir.is_dir():
        for path in sorted(unit_dir.iterdir()):
            if 'nailblog' not in path.name:
                continue
            if path.name.endswith('.d') and path.is_dir():
                for conf in sorted(path.glob('*.conf')):
                    dropins.append({'unit': path.name[:-2], **inspect_unit_file(conf)})
            elif path.name.endswith('.timer'):
                timers.append({'name': path.name, **inspect_unit_file(path)})
            elif path.name.endswith('.service'):
                units.append({'name': path.name, **inspect_unit_file(path)})
        for path in sorted(unit_dir.glob('*.d/*.conf')):   # drop-ins of units whose own name lacks "nailblog"
            owner = path.parent.name[:-2]
            if 'nailblog' not in owner and 'nailblog' in (read_text(path) or ''):
                dropins.append({'unit': owner, **inspect_unit_file(path)})
    for item in units + timers:
        item['enabled'] = systemctl(['is-enabled', item['name']]) if systemctl else None
        item['active'] = systemctl(['is-active', item['name']]) if systemctl else None
    failed = []
    if systemctl:
        raw = systemctl(['list-units', '--all', '--failed', '--plain', '--no-legend', '*nailblog*'])
        failed = [line.split()[0] for line in (raw or '').splitlines() if line.strip()]
    return {'units': units, 'dropins': dropins, 'timers': timers, 'failed': failed,
            'report_bridge_present': any(i['report_bridge'] for i in units + dropins + timers)}


def connect_ro(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)


def tables(db):
    return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def inspect_db(path):
    out = {'path': str(path), 'exists': Path(path).is_file(), 'control': None, 'onepost_missing_claims': None}
    if not out['exists']:
        return out
    try:
        db = connect_ro(path)
    except sqlite3.Error as error:
        out['error'] = str(error)
        return out
    try:
        db.row_factory = sqlite3.Row
        names = tables(db)
        if 'control' in names:
            out['control'] = [dict(row) for row in db.execute('SELECT * FROM control')]
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
    except sqlite3.Error as error:
        out['error'] = str(error)
    finally:
        db.close()
    return out


def ledger_digest(path):
    """sha256 of a canonical dump of paid_intents, read from a private copy so the ledger itself is never opened."""
    out = {'path': str(path), 'exists': Path(path).is_file(), 'paid_intents_rows': None, 'paid_intents_sha256': None}
    if not out['exists']:
        return out
    source = Path(path)
    with tempfile.TemporaryDirectory(prefix='nailblog-cutover-') as tmp:
        target = Path(tmp) / source.name
        shutil.copyfile(source, target)
        wal = Path(str(source) + '-wal')
        if wal.is_file():
            shutil.copyfile(wal, str(target) + '-wal')
        try:
            db = connect_ro(target)
        except sqlite3.Error as error:
            out['error'] = str(error)
            return out
        try:
            if 'paid_intents' in tables(db):
                cursor = db.execute('SELECT * FROM paid_intents ORDER BY 1')
                columns = [c[0] for c in cursor.description]
                rows = [list(map(_plain, row)) for row in cursor.fetchall()]
                dump = json.dumps({'columns': columns, 'rows': rows}, sort_keys=True, separators=(',', ':'))
                out['paid_intents_rows'] = len(rows)
                out['paid_intents_sha256'] = hashlib.sha256(dump.encode('utf-8')).hexdigest()
        except sqlite3.Error as error:
            out['error'] = str(error)
        finally:
            db.close()
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
        out['jobs'].append({
            'job': job.name,
            'files': sorted(p.name for p in job.iterdir()),
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


def inspect_config(path):
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
        'placeholder': sorted(slug for slug, guid in ids.items() if PLACEHOLDER.match(str(guid))),
        'all_placeholder': bool(ids) and all(PLACEHOLDER.match(str(g)) for g in ids.values())}
    out['slots'] = len((data.get('service') or {}).get('slots') or [])
    return out


def inspect_env(path):
    text = read_text(path)
    if text is None:
        return {'path': str(path), 'exists': False, 'gemini_image_key_present': None}
    present = any(line.strip().split('=', 1)[0].removeprefix('export ').strip() == GEMINI_KEY
                  for line in text.splitlines() if line.strip() and not line.lstrip().startswith('#'))
    return {'path': str(path), 'exists': True, 'gemini_image_key_present': present}


def collect(args, systemctl=run_systemctl):
    return {
        'systemd': inventory_units(args.unit_dir, None if args.no_systemctl else systemctl),
        'database': inspect_db(args.db),
        'queue': inspect_queue(args.queue, args.private_dir),
        'ledger': ledger_digest(args.ledger),
        'processes': inspect_processes(args.proc_root, args.lock),
        'config': inspect_config(args.config),
        'env': inspect_env(args.env_file),
    }


def parse(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    for name, default in DEFAULTS.items():
        parser.add_argument('--' + name.replace('_', '-'), default=default)
    parser.add_argument('--no-systemctl', action='store_true', help='skip enabled/active/failed (fixtures, non-systemd hosts)')
    return parser.parse_args(argv)


def main(argv=None, systemctl=run_systemctl):
    print(json.dumps(collect(parse(argv), systemctl), indent=2, sort_keys=True, default=str))
    return 0


if __name__ == '__main__':
    sys.exit(main())
