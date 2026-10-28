"""NailBlog CLI: offline preview pipelines and explicitly configured staging admin operations."""

import argparse
import asyncio
import logging
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from pathlib import Path

from ._errors import ConfigError
from .autoflow import RequeueRefused
from .config import load_config
from .schedule import VN_TZ, iso_slot, parse_hhmm, slot_datetime
from .service import CACHE_NAME, PreviewError
from .sources import SourceError
from .state import StateError
from .text import TextModelError
from .images import ImageError

PIPELINES = ('plan-week', 'preview', 'trends', 'daily')
DRY_RUN_COMMANDS = ('plan-week', 'trends', 'daily', 'autoflow-requeue')
DAILY_AT = {10: ('propose',), 13: ('decide', 'write'), 20: ('preview',)}


@dataclass
class Runtime:
    """Injection points for tests; None means the real network functions."""
    _test_transport: object = None   # TEST ONLY: scripted text model; main() always builds Runtime() so it is None
    opener: object = None      # called like urllib's opener(request, timeout=...)
    today: object = None       # datetime.date
    now: object = None         # epoch seconds, freezes the clock of trends/daily


@contextmanager
def scratch(config):
    """A throw-away state database (and blog cache) in a temporary directory outside the repo, so a --dry-run can do
    the whole job, including writing topics, posts and files, and leave nothing behind. When the real database
    exists it is copied; when it does not, the scratch state starts empty and the real state directory is never
    created or touched."""
    with tempfile.TemporaryDirectory(prefix='nailblog-dry-run-') as directory:
        copy = Path(directory) / 'nailblog.sqlite3'
        real = Path(config.db_path)
        if real.is_file():
            source, target = sqlite3.connect(real), sqlite3.connect(copy)
            try:
                source.backup(target)
            finally:
                source.close()
                target.close()
        cache = real.parent / CACHE_NAME
        if cache.is_file():
            shutil.copy2(cache, Path(directory) / CACHE_NAME)
        yield replace(config, db_path=str(copy))


@contextmanager
def _maybe_scratch(config, dry_run):
    if dry_run:
        with scratch(config) as copy:
            yield copy
    else:
        yield config


def _trends_report(items, board, note, dry_run, config):
    from .trends import format_traffic
    lines = [f"Google Trending US: {len(items)} mục lấy được"
             + (' (--dry-run: không lưu snapshot)' if dry_run else ' (đã lưu snapshot)')]
    if note:
        lines.append(note)
    rows = board.table(40)
    lines.append(f"Bảng điểm (hot 0.2 · đà 3 ngày 0.4 · đà 7 ngày 0.3 · TikTok 0.1), {len(rows)} dòng:")
    for row in rows:
        lines.append(f"- {row.term[:50]} | {format_traffic(row.traffic) if row.traffic else 'theo dõi'} | "
                     f"{row.total:.2f} | hot {row.hot:.2f} 3d {row.d3:.2f} 7d {row.d7:.2f} tt {row.tiktok:.2f}")
    if not rows:
        lines.append('(không có từ khóa liên quan nail/beauty và chưa có lịch sử cho từ khóa theo dõi)')
    return lines


async def _trends(config, args, runtime):
    from . import trends
    from .service import Service
    now = runtime.now if runtime.now is not None else time.time()
    with _maybe_scratch(config, args.dry_run) as cfg:
        service = Service(cfg, clock=lambda: now)
        items, note = trends.refresh(service.state, cfg, now, opener=runtime.opener,
                                     timeout=cfg.sources.timeout_seconds)
        board = trends.TrendBoard(service.state, cfg.trends, now)
        for line in _trends_report(items, board, note, args.dry_run, cfg):
            print(line)


async def _daily(config, args, runtime, options):
    from . import daily
    from .service import Service
    from .text import TextModel
    given = args.date or runtime.today
    first = DAILY_AT[args.at][0]
    section = {'propose': 'propose_at', 'decide': 'pick_deadline', 'preview': 'preview_at'}[first]
    if runtime.now is not None:
        now = runtime.now
    elif given:
        local = parse_hhmm(getattr(config.daily, section))
        now = datetime.combine(given, local, tzinfo=VN_TZ).timestamp()
    else:
        now = time.time()
    today = given or datetime.fromtimestamp(now, VN_TZ).date()
    real_today = datetime.fromtimestamp(runtime.now if runtime.now is not None else time.time(), VN_TZ).date()
    with _maybe_scratch(config, args.dry_run) as cfg:
        service = Service(cfg, clock=lambda: now)
        service.opener = runtime.opener
        service.refresh_trends = not (given and given < real_today)   # a past day never fetches today's real trends
        text = TextModel(cfg, **options)
        for step in DAILY_AT[args.at]:
            outcome = await daily.run_step(service, step, today, text, opener=runtime.opener)
            print(f'== daily {step} {today.isoformat()}')
            if not outcome.created:
                print('(đã chạy trước đó, không chạy lại; đây là DM đã ghi)')
            if outcome.text:
                print(outcome.text)
            if step == 'write':
                target = today + timedelta(days=1)
                low = iso_slot(slot_datetime(target, '00:00'))
                high = iso_slot(slot_datetime(target + timedelta(days=1), '00:00'))
                for post in service.state.list_posts(slot_from=low, slot_to=high):
                    print(f"bài #{post['id']} slot {post['slot_at']} [{post['state']}] {post['reason']}")
                if not outcome.complete:
                    print('(chưa viết đủ bài cho các slot đã chốt; service sẽ thử lại)')
            if outcome.created and outcome.text:
                print('(DM chưa gửi: ' + ('chế độ --dry-run' if args.dry_run else 'CLI không có phiên Telegram; '
                      'service sẽ gửi khi chạy trong ngày này') + ')')


def _autoflow_requeue(config, args, runtime):
    """Operator command (R7b): reopen a run held `text_quality_held` on a new slot. Bot must be paused."""
    from .autoflow import requeue_held_text
    from .service import Service
    now = runtime.now if runtime.now is not None else time.time()
    service = Service(config, clock=lambda: now)
    report = requeue_held_text(service, args.run, args.slot, now=now, dry_run=args.dry_run)
    print('autoflow-requeue' + (' --dry-run: không ghi gì' if report['dry_run'] else ': đã ghi'))
    print(f"run {report['run_id']} (chủ đề #{report['topic_id']}), requeued_by=operator")
    print(f"slot: {report['old_slot']} -> {report['new_slot']}")
    for label, key in (('chấm lại trước', 'previous_quality'), ('chấm lại bây giờ', 'rescored_quality')):
        quality = report[key]
        print(f"{label}: verdict={quality['verdict']} issues={','.join(quality['issues']) or '-'}")
    print(('sẽ đổi:' if report['dry_run'] else 'đã đổi:'))
    for line in report['changes']:
        print('  ' + line)


async def execute(command, config, args=None, runtime=None):
    runtime = runtime or Runtime()
    if command == 'pause-install':
        from .state import State
        state = State(config.db_path, image_mode=config.schedule.image_mode)
        state.set_paused(True)
        state.disarm()   # only an explicit /nailblog resume (with its preflight) arms again
        print('NailBlog installed/startup paused')
        return
    if command == 'startup-gate':
        from .state import startup_gate
        result = startup_gate(config, config_path=getattr(args, 'config', None))
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return
    if command in ('login', 'probe'):
        from .admin import Admin
        from .usage import write_atomic
        # Explicit CLI auth/probe capability; not a scheduler/live enable switch.
        async with Admin(replace(config, mode='probe')) as admin:
            if command == 'login':
                await admin.ready()
                print('NailBlog staging login verified')
            else:
                Path(args.out).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                report = await admin.probe(screenshot_path=Path(args.out).with_suffix('.png'))
                write_atomic(args.out, report)
                print('staging probe: authenticated=true editor_roundtrip=' + str(report['editor_roundtrip']).lower())
                print(args.out)
        return
    if command in ('run', 'login-telegram'):
        from .telegram import run, login_telegram
        await (run(config) if command == 'run' else login_telegram(config))
        return
    if command == 'status':
        from .service import Service
        for line in Service(config).command('status'):
            print(line)
        return
    if command == 'trends':
        await _trends(config, args, runtime)
        return
    if command == 'autoflow-requeue':
        _autoflow_requeue(config, args, runtime)
        return
    from .service import Service
    from .text import TextModel
    options = {'_test_transport': runtime._test_transport} if runtime._test_transport is not None else {}
    if command == 'daily':
        await _daily(config, args, runtime, options)
        return
    if command == 'plan-week':
        with _maybe_scratch(config, args.dry_run) as cfg:
            service = Service(cfg)
            text = TextModel(cfg, **options)
            outcome = await service.plan_week(text, opener=runtime.opener, dry_run=args.dry_run, today=runtime.today)
        print(outcome.text)
        if outcome.created and not outcome.sent:
            print('(DM chưa gửi: ' + ('chế độ --dry-run; đã làm trên bản sao state, không lưu gì' if args.dry_run
                                      else 'CLI không có phiên Telegram; chủ đề đã lưu, xem bằng /nailblog topics') + ')')
        return
    service = Service(config)
    text = TextModel(config, **options)
    outcome = await service.preview(args.topic, args.out, text)
    print(f'preview: verdict={outcome.verdict} words={outcome.words} issues={len(outcome.issues)}')
    for issue in outcome.issues:
        print(f"  [{issue['kind']}] {issue['code']}: {issue['message']}")
    print(outcome.json_path)
    print(outcome.html_path)


def main(argv=None, *, runtime=None):
    os.umask(0o077)
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')  # the DM and issue lists are Vietnamese
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog='python -m nailblog')
    parser.add_argument('command', choices=('run', 'login-telegram', 'status', 'login', 'probe', 'pause-install', 'startup-gate',
                                            'autoflow-requeue') + PIPELINES)
    parser.add_argument('--config', default=os.environ.get('NAILBLOG_CONFIG', '/etc/nailblog/nailblog.toml'))
    parser.add_argument('--dry-run', action='store_true',
                        help='plan-week, trends, daily: work on a scratch copy of the state (empty when none exists); nothing is written to the real state directory')
    parser.add_argument('--topic', type=int, help='preview: topic id')
    parser.add_argument('--out', help='preview: output directory; probe: output report JSON and sibling PNG')
    parser.add_argument('--run', help='autoflow-requeue: run id of the held run (e.g. auto-20261028-0600-t1)')
    parser.add_argument('--slot', help='autoflow-requeue: new slot, ISO datetime with timezone (e.g. 2026-10-28T14:00:00+07:00)')
    parser.add_argument('--at', type=int, choices=sorted(DAILY_AT), help='daily: milestone hour (10, 13 or 20)')
    parser.add_argument('--date', type=date.fromisoformat, help='daily: the run day, YYYY-MM-DD (default: today in Vietnam)')
    args = parser.parse_args(argv)
    if args.command == 'preview' and (args.topic is None or args.topic <= 0 or not args.out):
        parser.error('preview needs --topic <id> and --out <dir>')
    if args.command not in ('preview', 'probe') and (args.topic is not None or args.out):
        parser.error('--topic belongs to preview; --out belongs to preview/probe only')
    if args.command == 'probe' and (not args.out or args.topic is not None):
        parser.error('probe needs --out <report.json>')
    if args.command not in DRY_RUN_COMMANDS and args.dry_run:
        parser.error('--dry-run belongs to plan-week, trends, daily and autoflow-requeue only')
    if args.command == 'autoflow-requeue' and (not args.run or not args.slot):
        parser.error('autoflow-requeue needs --run <run_id> and --slot <ISO datetime with timezone>')
    if args.command != 'autoflow-requeue' and (args.run or args.slot):
        parser.error('--run and --slot belong to autoflow-requeue only')
    if args.command == 'daily' and args.at is None:
        parser.error('daily needs --at 10|13|20')
    if args.command != 'daily' and (args.at is not None or args.date is not None):
        parser.error('--at and --date belong to daily only')
    try:
        config = load_config(args.config)
        if not args.dry_run:   # a --dry-run never creates the real state directory
            Path(config.db_path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        asyncio.run(execute(args.command, config, args, runtime))
        return 0
    except (ConfigError, StateError) as error:
        print(str(error))
        return 2
    except RequeueRefused as error:
        print('requeue_refused: ' + error.code + (f' ({error.detail})' if error.detail else ''))
        return 1
    except (TextModelError, SourceError, PreviewError, ImageError) as error:
        print('NailBlog operation failed: ' + str(error))
        return 1
    except Exception as error:
        print('NailBlog operation failed: ' + type(error).__name__)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
