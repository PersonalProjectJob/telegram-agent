"""Offline agy text-job tests. Only the `agy` subprocess is replaced; worker/producer code is real.

Pure logic (collapse, salvage, heartbeat) and the worker/producer round trip run on any OS.
"""
from contextlib import closing
from datetime import datetime
from dataclasses import replace
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid

from nailblog.agy_text import AgyText, AgyTextError, classify
from nailblog.images import ImageError
from nailblog.images.agy_native import (collapse_json, native_text, salvage_text, assistant_text, user_prompt)
from nailblog.images.subscription import (Worker, Heartbeat, TEXT_PROTOCOL, PROTOCOL, text_policy, text_policy_sha,
                                          text_config_identity, config_identity, heartbeat_payload, ready_ok,
                                          beat_due, check_agent, text_job_id, validate_text_request,
                                          READY_MAX_AGE, atomic, read)
from nailblog.images.subscription_io import Directory
from nailblog.model_lock import MODEL_LOCK, LockStopped
from nailblog.publication import digest
from tests.test_subscription import config, proto

SCHEMA = {'type': 'object', 'required': ['title'], 'properties': {'title': {'type': 'string', 'minLength': 1}}}
ARTICLE = {'title': 'Nail nau sua'}
SESSION = '11111111-2222-4333-8444-555555555555'


def user_record(prompt):
    p = prompt.encode()
    return proto(f1=14, f4=3, f5=proto(f1=b'm'), f19=proto(f2=p, f3=proto(f1=p), f4=b'', f12=b'x'))


def assistant_record(text, *, tool=False, extra=False):
    t = text.encode()
    meta = proto(f1=b'm', f4=b'call') if tool else proto(f1=b'm')
    body = dict(f1=t, f6=b'model', f8=t, f12=2, f14=proto(f2=b'sig'))
    if extra: body['f9'] = b'surprise'
    return proto(f1=15, f4=3, f5=meta, f20=proto(**body))


def rows_for(prompt, *answers, tool=False, extra=False):
    rows = [(0, 14, 3, 0, user_record(prompt), None, None)]
    for i, answer in enumerate(answers, 1):
        rows.append((i, 15, 3, 0, assistant_record(answer, tool=tool, extra=extra), None, None))
    return rows


def write_conversation(path, rows):
    with closing(sqlite3.connect(path)) as db:
        db.execute('CREATE TABLE steps (idx INTEGER, step_type INTEGER, status INTEGER, step_format INTEGER, '
                   'step_payload BLOB, permissions BLOB, error_details BLOB)')
        db.executemany('INSERT INTO steps VALUES (?,?,?,?,?,?,?)', rows)
        db.commit()


def peek(path):
    """Read a file another thread replaces atomically; Windows may refuse the read mid-replace."""
    for _ in range(200):
        try: return json.loads(Path(path).read_bytes())
        except (PermissionError, FileNotFoundError, ValueError): time.sleep(0.005)
    raise AssertionError('worker-ready.json unreadable')


class Clock:
    def __init__(self, t=1_000_000.0): self.t = t
    def __call__(self): return self.t


class CollapseTests(unittest.TestCase):
    def test_four_identical_copies_become_one(self):
        raw = json.dumps(ARTICLE)
        self.assertEqual(collapse_json('\n'.join([raw] * 4)), (ARTICLE, 4))
        self.assertEqual(collapse_json(raw), (ARTICLE, 1))

    def test_different_copies_garbage_and_non_objects_hold(self):
        for text in (json.dumps(ARTICLE) + json.dumps({'title': 'other'}), json.dumps(ARTICLE) + ' junk', '', '[1]', '"x"',
                     json.dumps(ARTICLE) + json.dumps({'title': 'Nail nau sua'}, indent=1)):
            with self.subTest(text=text[:30]), self.assertRaises(ImageError) as caught:
                collapse_json(text)
            self.assertTrue(str(caught.exception).startswith('cli_text_held_'))
        with self.assertRaises(ImageError): collapse_json(None)
        with self.assertRaises(ImageError): collapse_json('\n'.join([json.dumps(ARTICLE)] * 9))


class NativeTextTests(unittest.TestCase):
    def test_valid_rows_and_salvage(self):
        rows = rows_for('P', json.dumps(ARTICLE))
        native = native_text(rows, 'P')
        self.assertEqual((native['final_text'], native['steps']), (json.dumps(ARTICLE), [14, 15]))
        self.assertEqual(salvage_text(rows, 'P')['final_text'], json.dumps(ARTICLE))
        self.assertEqual(salvage_text(rows_for('P', 'thinking', json.dumps(ARTICLE)), 'P')['steps'], [14, 15, 15])

    def test_unknown_native_shapes_hold_and_salvage_refuses(self):
        good = rows_for('P', json.dumps(ARTICLE))
        bad = {
            'wrong prompt': rows_for('Q', json.dumps(ARTICLE)),
            'tool call': rows_for('P', json.dumps(ARTICLE), tool=True),
            'extra field': rows_for('P', json.dumps(ARTICLE), extra=True),
            'no answer': good[:1],
            'ends on user': good + [(2, 14, 3, 0, user_record('P'), None, None)],
            'unknown step type': [good[0], (1, 132, 3, 0, good[1][4], None, None)],
            'status': [good[0], (1, 15, 1, 0, good[1][4], None, None)],
            'permissions': [good[0], (1, 15, 3, 0, good[1][4], b'x', None)],
            'gap': [good[0], (2, 15, 3, 0, good[1][4], None, None)],
            'garbage': [good[0], (1, 15, 3, 0, b'\xff' * 8, None, None)],
        }
        for name, rows in bad.items():
            with self.subTest(name):
                with self.assertRaises(ImageError) as caught: native_text(rows, 'P')
                self.assertTrue(str(caught.exception).startswith('cli_text_held_'), str(caught.exception))
                self.assertIsNone(salvage_text(rows, 'P'))
        with self.assertRaises(ImageError): assistant_text(b'x' * 10)
        with self.assertRaises(ImageError): user_prompt(good[1][4], 'P')


class PolicyAndIdentityTests(unittest.TestCase):
    def test_policy_has_no_tools_and_matches_deploy_file(self):
        body = text_policy('nailblog-text-only')
        for needle in (b'tools: []', b'subagent: false', b'commandExecutionPolicy: "off"', b'name: nailblog-text-only'):
            self.assertIn(needle, body)
        self.assertNotIn(b'accept', body.lower())
        deployed = (Path(__file__).parents[1] / 'deploy/nailblog-text-only.md').read_bytes().replace(b'\r\n', b'\n')
        self.assertEqual(deployed, body)
        self.assertNotEqual(text_policy_sha('nailblog-text-only'), text_policy_sha('other-agent'))

    def test_agent_name_validation(self):
        for good in ('nailblog-text-only', 'a', '0x', 'a' * 64): self.assertEqual(check_agent(good), good)
        for bad in ('', '-a', 'A', 'a_b', 'a b', 'a' * 65, 'a\n', '../x', 'a/b', None, 5):
            with self.subTest(bad=bad), self.assertRaises(ImageError): check_agent(bad)

    def test_request_validation_binds_job_id_config_and_schema(self):
        with tempfile.TemporaryDirectory() as root:
            cfg = config(root).subscription
            job = text_job_id('p', 'prompt', SCHEMA)
            req = {'protocol': TEXT_PROTOCOL, 'kind': 'text', 'job_id': job, 'purpose': 'p', 'prompt': 'prompt', 'schema': SCHEMA,
                   'attempt': 0, 'expires_at': 5, 'config': text_config_identity(cfg)}
            validate_text_request(req, job, cfg)
            for change in ({'prompt': 'other'}, {'purpose': 'q'}, {'kind': 'image'}, {'extra': 1}, {'expires_at': float('inf')},
                           {'config': {**text_config_identity(cfg), 'text_agent': 'x'}}, {'schema': {}}):
                with self.subTest(change=change), self.assertRaises(ImageError):
                    validate_text_request({**req, **change}, job, cfg)
            with self.assertRaises(ImageError):
                validate_text_request(req, job, replace(cfg, text_agent='Bad Name'))


class HeartbeatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.cfg = config(self.temp.name).subscription
        self.ready = Path(self.cfg.queue_dir) / 'worker-ready.json'

    def test_pure_schedule_and_freshness(self):
        self.assertTrue(beat_due(None, 0)); self.assertFalse(beat_due(100, 114)); self.assertTrue(beat_due(100, 115))
        self.assertTrue(beat_due(100, 50))
        busy = 'a' * 64
        # A 300s job beating every 15s stays ready at every moment; the old single stamp did not.
        beats = [i * 15 for i in range(21)]
        for now in range(0, 301):
            at = max(b for b in beats if b <= now)
            self.assertTrue(ready_ok(heartbeat_payload(self.cfg, at, busy), self.cfg, now), now)
        stale = heartbeat_payload(self.cfg, 0, busy)
        self.assertTrue(ready_ok(stale, self.cfg, READY_MAX_AGE))
        self.assertFalse(ready_ok(stale, self.cfg, READY_MAX_AGE + 1))   # dead worker: fail closed
        self.assertFalse(ready_ok(stale, self.cfg, -1))
        for broken in ({'busy_job': 'zz'}, {'busy_job': 5}, {'protocol': 'x'}, {'config': {}}, {'at': 'now'}, {'at': float('nan')}):
            self.assertFalse(ready_ok({**heartbeat_payload(self.cfg, 0), **broken}, self.cfg, 1), broken)
        self.assertFalse(ready_ok(heartbeat_payload(self.cfg, 0, text=False), self.cfg, 1, text=True))
        self.assertTrue(ready_ok(heartbeat_payload(self.cfg, 0, text=False), self.cfg, 1))

    def test_heartbeat_thread_refreshes_and_reports_busy_job(self):
        seen = []
        with Heartbeat(self.cfg, interval=0.02) as beat:
            beat.set_busy('b' * 64)
            deadline = time.monotonic() + 5
            while len({s['at'] for s in seen}) < 4 and time.monotonic() < deadline:
                seen.append(peek(self.ready)); time.sleep(0.01)
            self.assertEqual(seen[-1]['busy_job'], 'b' * 64)
            beat.set_busy(None)
        self.assertGreaterEqual(len({s['at'] for s in seen}), 4)
        self.assertIsNone(peek(self.ready)['busy_job'])
        self.assertTrue(ready_ok(peek(self.ready), self.cfg, time.time()))


class Env(unittest.TestCase):
    """Worker + producer over a temp queue with only the agy subprocess faked."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.c = config(root)
        self.cfg = self.c.subscription
        self.private = Path(self.cfg.private_dir)
        self.home = root / 'home'
        agents = self.home / 'snap/antigravity-cli/common/.gemini/config/agents'; agents.mkdir(parents=True)
        (agents / 'nailblog-text-only.md').write_bytes(text_policy('nailblog-text-only'))
        self.conversations = self.home / 'snap/antigravity-cli/common/.gemini/antigravity-cli/conversations'
        self.conversations.mkdir(parents=True)
        locks = root / 'locks'; locks.mkdir()
        patcher = patch.dict(os.environ, {'AGENTFLEET_LOCK_DIR': str(locks)}); patcher.start(); self.addCleanup(patcher.stop)
        self.clock = Clock()
        self.calls = []
        self.on_agent = None
        self.behaviour = self.answer
        self.worker = Worker(self.cfg, runner=self.runner, clock=self.clock, home=self.home)
        self.ask = AgyText(self.c, clock=self.clock, sleep=self.tick)
        self.sleeps = 0
        self.during_wait = []

    # --- fake agy subprocess ---
    def runner(self, args, *, cwd, timeout):
        if args[1:] == ['--version']: return b'agy 1.2.11\n'
        if args[1:] == ['agent']:
            hook, self.on_agent = self.on_agent, None
            if hook: hook()
            return b'nailblog-text-only\nnailblog-image-only\n'
        self.calls.append((list(args), timeout))
        return self.behaviour(args, timeout)

    def conversation(self, prompt, *answers, **kw):
        session = str(uuid.uuid4())
        write_conversation(self.conversations / (session + '.db'), rows_for(prompt, *answers, **kw))
        return session

    def envelope(self, session, response, *, copies=4):
        text = json.dumps({'conversation_id': session, 'status': 'SUCCESS', 'response': response, 'denied_actions': []})
        return '\n'.join([text] * copies).encode()

    def answer(self, args, timeout):
        prompt = args[args.index('--print') + 1]
        body = json.dumps(ARTICLE)
        return self.envelope(self.conversation(prompt, body), '\n'.join([body] * 4))

    # --- fake time: sleeping lets the "worker" process one job ---
    def tick(self, seconds):
        self.sleeps += 1
        self.clock.t += seconds
        for check in self.during_wait: check()
        if self.sleeps == 1 and self.autorun:
            with Directory(self.cfg.queue_dir) as queue: self.worker.scan(queue, self.private)

    autorun = True

    def beat(self, busy=None, at=None):
        atomic(Path(self.cfg.queue_dir) / 'worker-ready.json', heartbeat_payload(self.cfg, self.clock() if at is None else at, busy))

    def submit(self):
        self.beat()
        return self.ask.ask('write json', SCHEMA, purpose='run1:write')

    def markers(self):
        return [json.loads(p.read_bytes()) for p in self.private.glob('*.json')]


class WorkerTextTests(Env):
    def run_job(self, prompt='write json', purpose='run1:write'):
        """Place a text job directly in the queue and run one worker scan."""
        job_id = text_job_id(purpose, prompt, SCHEMA)
        path = Path(self.cfg.queue_dir) / job_id; path.mkdir(mode=0o700)
        atomic(path / 'request.json', {'protocol': TEXT_PROTOCOL, 'kind': 'text', 'job_id': job_id, 'purpose': purpose, 'prompt': prompt,
                                       'schema': SCHEMA, 'attempt': 0, 'expires_at': self.clock() + 5000, 'config': text_config_identity(self.cfg)})
        with Directory(self.cfg.queue_dir) as queue:
            result = self.worker.scan(queue, self.private)
        return job_id, result

    def installed_policy(self):
        return self.home / 'snap/antigravity-cli/common/.gemini/config/agents/nailblog-text-only.md'

    def tamper(self):
        path = self.installed_policy()
        path.write_bytes(path.read_bytes().replace(b'tools: []', b'tools: [a]'))

    def test_modified_installed_policy_blocks_dispatch(self):
        self.tamper()
        job_id, result = self.run_job()
        self.assertEqual(result['error'], 'cli_tool_policy_changed')
        self.assertEqual(self.calls, [])

    def test_policy_changed_between_preflight_and_lock_blocks_dispatch(self):
        self.on_agent = self.tamper          # fires during the pre-lock discovery listing
        job_id, result = self.run_job()
        self.assertEqual(result['error'], 'cli_tool_policy_changed')
        self.assertEqual(self.calls, [])

    def test_heartbeat_failure_never_rejects_a_valid_job(self):
        class Broken:
            last_error = None
            def set_busy(self, job): raise OSError('disk full')
        self.worker.beat = Broken()
        job_id, result = self.run_job()
        self.assertEqual(result['status'], 'complete', result)
        self.assertEqual(self.worker.beat.last_error, 'OSError')

    def test_oversized_request_is_rejected_before_dispatch(self):
        big = 'ạ' * 40000
        job_id = text_job_id('big', big, SCHEMA)
        path = Path(self.cfg.queue_dir) / job_id; path.mkdir()
        atomic(path / 'request.json', {'protocol': TEXT_PROTOCOL, 'kind': 'text', 'job_id': job_id, 'purpose': 'big',
                                       'prompt': big, 'schema': SCHEMA, 'attempt': 0, 'expires_at': self.clock() + 5000,
                                       'config': text_config_identity(self.cfg)})
        with Directory(self.cfg.queue_dir) as queue: result = self.worker.scan(queue, self.private)
        self.assertEqual((result['status'], result['error']), ('rejected', 'cli_request_invalid'))
        self.assertEqual(self.calls, [])
        self.assertEqual([m for m in self.markers() if m['status'] == 'dispatched'], [])

    def test_process_failure_after_complete_answer_is_salvaged_but_other_errors_are_not(self):
        def failed_after_answer(args, timeout):
            self.conversation(args[args.index('--print') + 1], json.dumps(ARTICLE)); raise ImageError('cli_process_failed_unknown')
        self.behaviour = failed_after_answer
        job_id, result = self.run_job(prompt='crash')
        self.assertEqual((result['status'], result['salvaged_from']), ('complete_salvaged', 'cli_process_failed_unknown'))
        def output_limit(args, timeout):
            self.conversation(args[args.index('--print') + 1], json.dumps(ARTICLE)); raise ImageError('cli_output_limit_unknown')
        self.behaviour = output_limit
        job_id, result = self.run_job(prompt='limit')
        self.assertEqual((result['status'], result['error']), ('unknown', 'cli_output_limit_unknown'))

    def test_four_identical_stdout_copies_collapse_and_receipt_has_provenance(self):
        job_id, result = self.run_job()
        self.assertEqual(result['status'], 'complete', result)
        self.assertEqual((result['stdout_copies'], result['tool_calls_observed'], result['native_images']), (4, 0, 0))
        command = self.calls[0][0]
        self.assertEqual(command[command.index('--agent') + 1], 'nailblog-text-only')
        self.assertEqual(command[command.index('--print-timeout') + 1], '585s')
        self.assertEqual(self.calls[0][1], 600)
        self.assertNotIn('--accept-edits', command)
        self.assertEqual(result['prompt_sha'], __import__('hashlib').sha256(b'write json').hexdigest())
        self.assertEqual(result['policy_sha'], text_policy_sha('nailblog-text-only'))
        self.assertEqual(json.loads((Path(self.cfg.queue_dir) / job_id / 'output.json').read_bytes()), ARTICLE)
        self.assertEqual(len(self.calls), 1)

    def test_different_stdout_copies_or_unknown_native_rows_hold_without_retry(self):
        def differing(args, timeout):
            prompt = args[args.index('--print') + 1]
            session = self.conversation(prompt, json.dumps(ARTICLE))
            return self.envelope(session, json.dumps(ARTICLE) + json.dumps({'title': 'Other'}))
        def tool_row(args, timeout):
            prompt = args[args.index('--print') + 1]
            return self.envelope(self.conversation(prompt, json.dumps(ARTICLE), tool=True), json.dumps(ARTICLE))
        def native_disagrees(args, timeout):
            prompt = args[args.index('--print') + 1]
            return self.envelope(self.conversation(prompt, json.dumps({'title': 'native'})), json.dumps(ARTICLE))
        for index, behaviour in enumerate((differing, tool_row, native_disagrees)):
            with self.subTest(behaviour.__name__):
                self.behaviour = behaviour; self.calls.clear()
                job_id, result = self.run_job(prompt='p%d' % index)
                self.assertEqual(result['status'], 'unknown')
                self.assertTrue(result['error'].startswith('cli_text_held_'), result)
                self.assertEqual(classify(result['error']), 'held')
                self.assertEqual(len(self.calls), 1)
                with Directory(self.cfg.queue_dir) as queue:
                    self.assertIsNone(self.worker.scan(queue, self.private))   # marker stops a rerun
                self.assertEqual(len(self.calls), 1)

    def test_timeout_with_final_answer_is_salvaged_once(self):
        def timeout_after_answer(args, timeout):
            self.conversation(args[args.index('--print') + 1], 'thinking...', json.dumps(ARTICLE))
            raise ImageError('cli_timeout_unknown')
        self.behaviour = timeout_after_answer
        job_id, result = self.run_job()
        self.assertEqual(result['status'], 'complete_salvaged', result)
        self.assertEqual(result['native_steps'], [14, 15, 15])
        self.assertEqual(json.loads((Path(self.cfg.queue_dir) / job_id / 'output.json').read_bytes()), ARTICLE)
        with Directory(self.cfg.queue_dir) as queue: self.assertIsNone(self.worker.scan(queue, self.private))
        self.assertEqual(len(self.calls), 1)

    def test_timeout_without_valid_answer_is_unknown_and_never_redispatched(self):
        cases = {
            'no conversation': lambda p: None,
            'user only': lambda p: write_conversation(self.conversations / (str(uuid.uuid4()) + '.db'), rows_for(p)),
            'answer not schema-valid': lambda p: self.conversation(p, json.dumps({'nope': 1})),
            'answer not json': lambda p: self.conversation(p, 'half an artic'),
            'tool call row': lambda p: self.conversation(p, json.dumps(ARTICLE), tool=True),
            'two fresh conversations': lambda p: (self.conversation(p, json.dumps(ARTICLE)), self.conversation(p, json.dumps(ARTICLE))),
        }
        for index, (name, make) in enumerate(cases.items()):
            with self.subTest(name):
                def behaviour(args, timeout, make=make):
                    make(args[args.index('--print') + 1]); raise ImageError('cli_timeout_unknown')
                self.behaviour = behaviour; self.calls.clear()
                job_id, result = self.run_job(prompt='case %d' % index)
                self.assertEqual((result['status'], result['error']), ('unknown', 'cli_timeout_unknown'))
                with Directory(self.cfg.queue_dir) as queue: self.assertIsNone(self.worker.scan(queue, self.private))
                self.assertEqual(len(self.calls), 1)

    def test_daily_cap_is_shared_with_image_jobs_and_blocks_dispatch(self):
        day = __import__('datetime').datetime.fromtimestamp(self.clock(), __import__('nailblog.schedule', fromlist=['VN_TZ']).VN_TZ).date().isoformat()
        for i in range(self.cfg.max_daily_jobs):   # image-job markers
            atomic(self.private / ('%064x.json' % i), {'day': day, 'run_id': 'img-run', 'status': 'dispatched'})
        job_id, result = self.run_job()
        self.assertEqual((result['status'], result['error']), ('rejected', 'cli_daily_job_cap'))
        self.assertEqual(self.calls, [])
        self.assertEqual(classify(result['error']), 'daily_cap')

    def test_text_dispatch_counts_toward_the_cap_like_an_image(self):
        self.run_job(prompt='one')
        marker = [m for m in self.markers() if m.get('kind') == 'text']
        self.assertEqual(len(marker), 1)
        self.assertEqual(marker[0]['status'], 'dispatched')

    def test_old_image_v1_job_and_unknown_marker_job_never_run_again(self):
        queue_path = Path(self.cfg.queue_dir)
        legacy = 'ccc058' + '0' * 58
        done = 'd' * 64
        for name in (legacy, done):
            (queue_path / name).mkdir(mode=0o700)
            atomic(queue_path / name / 'request.json', {'protocol': PROTOCOL})
        atomic(self.private / (legacy + '.json'), {'day': 'x', 'run_id': 'r', 'status': 'unknown'})
        atomic(queue_path / done / 'receipt.json', {'status': 'complete'})        # v1 job finished, marker missing
        generate = patch.object(self.worker, 'generate', side_effect=AssertionError('image job re-dispatched'))
        text = patch.object(self.worker, 'generate_text', side_effect=AssertionError('re-dispatched'))
        with generate, text, Directory(self.cfg.queue_dir) as queue:
            self.assertIsNone(self.worker.scan(queue, self.private))
        self.assertEqual(self.calls, [])

    def test_queue_is_fifo_by_age(self):
        queue_path = Path(self.cfg.queue_dir)
        names = ['f' * 64, '0' * 64, '8' * 64]
        for age, name in enumerate(names):
            (queue_path / name).mkdir(); os.utime(queue_path / name, (1000 + age, 1000 + age))
        with Directory(self.cfg.queue_dir) as queue:
            self.assertEqual(self.worker.fifo(queue), names)

    def test_invalid_text_agent_is_rejected_before_dispatch(self):
        bad = Worker(replace(self.cfg, text_agent='Bad Agent'), runner=self.runner, clock=self.clock, home=self.home)
        job_id = 'e' * 64
        (Path(self.cfg.queue_dir) / job_id).mkdir()
        atomic(Path(self.cfg.queue_dir) / job_id / 'request.json', {'protocol': TEXT_PROTOCOL})
        with Directory(self.cfg.queue_dir) as queue: result = bad.scan(queue, self.private)
        self.assertEqual(result['status'], 'rejected')
        self.assertEqual(self.calls, [])

    def test_heartbeat_keeps_refreshing_while_runner_is_blocked(self):
        worker = Worker(self.cfg, runner=self.runner, clock=time.time, home=self.home, heartbeat_seconds=0.02)
        ready = Path(self.cfg.queue_dir) / 'worker-ready.json'
        observed, release = [], threading.Event()

        def blocked(args, timeout):
            deadline = time.monotonic() + 5
            while len({o['at'] for o in observed}) < 4 and time.monotonic() < deadline:
                observed.append(peek(ready)); time.sleep(0.01)
            release.set()
            return self.answer(args, timeout)
        self.behaviour = blocked
        job_id = text_job_id('hb', 'p', SCHEMA)
        path = Path(self.cfg.queue_dir) / job_id; path.mkdir()
        atomic(path / 'request.json', {'protocol': TEXT_PROTOCOL, 'kind': 'text', 'job_id': job_id, 'purpose': 'hb', 'prompt': 'p',
                                       'schema': SCHEMA, 'attempt': 0, 'expires_at': time.time() + 5000, 'config': text_config_identity(self.cfg)})
        with Heartbeat(self.cfg, clock=time.time, interval=0.02) as beat:
            worker.beat = beat
            with Directory(self.cfg.queue_dir) as queue: result = worker.scan(queue, self.private)
        self.assertEqual(result['status'], 'complete', result)
        self.assertTrue(release.is_set())
        self.assertGreaterEqual(len({o['at'] for o in observed}), 4)
        self.assertTrue(all(o['busy_job'] == job_id for o in observed))
        self.assertTrue(all(ready_ok(o, self.cfg, o['at'] + 1, text=True) for o in observed))
        self.assertIsNone(peek(ready)['busy_job'])


class AgyTextTests(Env):
    def test_ask_round_trip_returns_json_with_receipt_and_never_holds_model_lock(self):
        state = []

        def lock_is_free():
            class Probe:
                n = 0
                def is_set(self):
                    self.n += 1
                    return self.n > 1
                def wait(self, seconds): return self.is_set()
            try:
                with MODEL_LOCK.enter(background=True, stop=Probe()): state.append('free')
            except LockStopped: state.append('held')
        self.during_wait.append(lock_is_free)
        self.assertEqual(self.submit(), ARTICLE)
        self.assertEqual(state, ['free'])
        self.assertEqual(self.ask.last_receipt['status'], 'complete')
        self.assertEqual(self.ask.last_receipt['native_steps'], [14, 15])

    def test_lock_probe_detects_a_held_lock(self):
        from nailblog.model_lock import ModelLock
        class Probe:
            n = 0
            def is_set(self):
                self.n += 1
                return self.n > 1
            def wait(self, seconds): return self.is_set()
        with MODEL_LOCK.enter(background=True):
            with self.assertRaises(LockStopped):
                with ModelLock().enter(background=True, stop=Probe()): pass

    def test_not_ready_without_fresh_heartbeat_fail_closed(self):
        with self.assertRaises(AgyTextError) as caught: self.ask.ask('p', SCHEMA, purpose='x')   # no ready file
        self.assertEqual(caught.exception.code, 'not_ready')
        self.beat(at=self.clock() - READY_MAX_AGE - 1)
        with self.assertRaises(AgyTextError) as caught: self.ask.ask('p', SCHEMA, purpose='x')
        self.assertEqual(caught.exception.code, 'not_ready')
        self.assertEqual(list(p for p in Path(self.cfg.queue_dir).iterdir() if p.is_dir()), [])

    def test_busy_worker_with_fresh_heartbeat_is_ready_and_job_waits_fifo(self):
        self.beat(busy='a' * 64)
        self.autorun = True
        self.assertEqual(self.ask.ask('write json', SCHEMA, purpose='busy'), ARTICLE)

    def test_unknown_is_final_same_purpose_never_creates_a_second_job(self):
        def timeout(args, t): raise ImageError('cli_timeout_unknown')
        self.behaviour = timeout
        with self.assertRaises(AgyTextError) as caught: self.submit()
        self.assertEqual((caught.exception.code, caught.exception.detail), ('unknown', 'cli_timeout_unknown'))
        self.autorun = False
        with self.assertRaises(AgyTextError) as again: self.submit()
        self.assertEqual(again.exception.code, 'unknown')
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len([p for p in Path(self.cfg.queue_dir).iterdir() if p.is_dir()]), 1)

    def test_transient_rejection_gets_a_new_attempt_job_once_the_day_changes(self):
        from nailblog.schedule import VN_TZ
        day = datetime.fromtimestamp(self.clock(), VN_TZ).date().isoformat()
        for i in range(self.cfg.max_daily_jobs):
            atomic(self.private / ('%064x.json' % i), {'day': day, 'run_id': 'img', 'status': 'dispatched'})
        with self.assertRaises(AgyTextError) as caught: self.submit()
        self.assertEqual(caught.exception.code, 'daily_cap')
        jobs = lambda: len([p for p in Path(self.cfg.queue_dir).iterdir() if p.is_dir()])
        self.assertEqual(jobs(), 1)
        self.sleeps = 0
        with self.assertRaises(AgyTextError) as same_day: self.submit()      # same day: stays the same rejected job
        self.assertEqual((same_day.exception.code, jobs()), ('daily_cap', 1))
        self.clock.t += 86400
        self.sleeps = 0
        self.assertEqual(self.submit(), ARTICLE)                            # next day: one new attempt, runs once
        self.assertEqual((len(self.calls), jobs()), (1, 2))
        self.sleeps = 0; self.autorun = False
        self.assertEqual(self.submit(), ARTICLE)                            # complete is final: reattach, no 3rd job
        self.assertEqual((len(self.calls), jobs()), (1, 2))

    def test_expired_rejection_retries_but_complete_never_does(self):
        self.assertEqual(self.submit(), ARTICLE)
        job = next(p for p in Path(self.cfg.queue_dir).iterdir() if p.is_dir())
        receipt = json.loads((job / 'receipt.json').read_bytes())
        atomic(job / 'receipt.json', {**receipt, 'status': 'rejected', 'error': 'cli_job_expired'})
        self.sleeps = 0
        self.assertEqual(self.submit(), ARTICLE)
        self.assertEqual(len([p for p in Path(self.cfg.queue_dir).iterdir() if p.is_dir()]), 2)

    def test_oversized_prompt_is_invalid_with_no_job_no_marker_no_dispatch(self):
        self.beat()
        with self.assertRaises(AgyTextError) as caught: self.ask.ask('ạ' * 40000, SCHEMA, purpose='big')
        self.assertEqual(caught.exception.code, 'invalid')
        with self.assertRaises(AgyTextError) as caught: self.ask.ask('x', {'t': 'ạ' * 40000}, purpose='big')
        self.assertEqual(caught.exception.code, 'invalid')
        self.assertEqual(([p for p in Path(self.cfg.queue_dir).iterdir() if p.is_dir()], self.markers(), self.calls), ([], [], []))

    def test_job_dir_without_request_is_repaired_not_failed(self):
        (Path(self.cfg.queue_dir) / text_job_id('run1:write', 'write json', SCHEMA, 0)).mkdir()
        self.assertEqual(self.submit(), ARTICLE)
        self.assertEqual(len(self.calls), 1)

    def test_salvaged_answer_is_returned_and_flagged(self):
        def timeout_after_answer(args, t):
            self.conversation(args[args.index('--print') + 1], json.dumps(ARTICLE)); raise ImageError('cli_timeout_unknown')
        self.behaviour = timeout_after_answer
        self.assertEqual(self.submit(), ARTICLE)
        self.assertEqual(self.ask.last_receipt['status'], 'complete_salvaged')

    def test_error_codes(self):
        def different(args, t):
            return self.envelope(self.conversation(args[args.index('--print') + 1], json.dumps(ARTICLE)), json.dumps({'title': 'x'}))
        self.behaviour = different
        with self.assertRaises(AgyTextError) as caught: self.submit()
        self.assertEqual(caught.exception.code, 'held')
        self.assertEqual({classify(e): 1 for e in ('cli_daily_job_cap', 'cli_job_expired', 'cli_text_schema_mismatch', 'weird')},
                         {'daily_cap': 1, 'expired': 1, 'schema': 1, 'unknown': 1})

    def test_daily_cap_surfaces(self):
        day = __import__('datetime').datetime.fromtimestamp(self.clock(), __import__('nailblog.schedule', fromlist=['VN_TZ']).VN_TZ).date().isoformat()
        for i in range(self.cfg.max_daily_jobs):
            atomic(self.private / ('%064x.json' % i), {'day': day, 'run_id': 'img', 'status': 'dispatched'})
        with self.assertRaises(AgyTextError) as caught: self.submit()
        self.assertEqual(caught.exception.code, 'daily_cap')
        self.assertEqual(self.calls, [])

    def test_tampered_output_or_receipt_is_rejected(self):
        self.assertEqual(self.submit(), ARTICLE)
        job = next(p for p in Path(self.cfg.queue_dir).iterdir() if p.is_dir())
        atomic(job / 'output.json', {'title': 'forged'})
        self.autorun = False
        with self.assertRaises(AgyTextError) as caught: self.submit()
        self.assertEqual(caught.exception.code, 'failed')

    def test_no_receipt_by_limit_cancels_and_reports_unknown(self):
        self.autorun = False
        with self.assertRaises(AgyTextError) as caught: self.submit()
        self.assertEqual(caught.exception.code, 'unknown')
        job = next(p for p in Path(self.cfg.queue_dir).iterdir() if p.is_dir())
        self.assertTrue((job / 'cancelled.json').exists())
        self.assertEqual(self.calls, [])

    def test_invalid_arguments_and_expired_deadline(self):
        self.beat()
        for args, kw in ((('', SCHEMA), {'purpose': 'p'}), (('x', []), {'purpose': 'p'}), (('x', SCHEMA), {'purpose': 'bad purpose'}),
                         (('x', SCHEMA), {'purpose': ''})):
            with self.subTest(args=args, kw=kw), self.assertRaises(AgyTextError) as caught: self.ask.ask(*args, **kw)
            self.assertEqual(caught.exception.code, 'invalid')
        with self.assertRaises(AgyTextError) as caught: self.ask.ask('x', SCHEMA, purpose='p', deadline=self.clock() - 1)
        self.assertEqual(caught.exception.code, 'expired')


if __name__ == '__main__':
    unittest.main()
