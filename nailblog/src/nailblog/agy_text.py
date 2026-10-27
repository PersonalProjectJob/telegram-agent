"""Producer side of agy text jobs (protocol nailblog-agy-text-v1).

The bot submits a job into the subscription queue and waits for the worker's
receipt. It NEVER holds MODEL_LOCK while waiting: the worker takes the shared
lock itself, so a bot-held lock would deadlock. A job that ended `unknown` is
final; callers decide whether to ask again with a different `purpose`.
"""
from datetime import datetime
import json
import time

from .images import ImageError
from .images.subscription import (Directory, TEXT_PROTOCOL, PURPOSE_RE, MAX_TEXT_ARGV_BYTES, TRANSIENT_REJECTIONS,
                                  check_agent, check_ready, text_argv_bytes, text_config_identity, text_job_id, uid,
                                  validate_text_request, verify_text_receipt)
from .publication import digest
from .schedule import VN_TZ

POLL_SECONDS = 1
WAIT_MARGIN_SECONDS = 30
MAX_ATTEMPTS = 16


class AgyTextError(Exception):
    """`code`: unknown | not_ready | held | daily_cap | expired | cancelled | invalid | schema | failed.

    `detail` carries the original receipt/worker error code (e.g. cli_timeout_unknown).
    """

    def __init__(self, code, detail=''):
        super().__init__(code + (': ' + detail if detail else ''))
        self.code, self.detail = code, detail


def classify(error):
    """Map a worker/producer error string to a stable AgyTextError code."""
    error = str(error or '')
    if error.startswith('cli_text_held'): return 'held'
    for prefix, code in (('cli_daily_job_cap', 'daily_cap'), ('cli_job_expired', 'expired'),
                         ('cli_job_cancelled', 'cancelled'), ('cli_request_invalid', 'invalid'),
                         ('cli_text_schema_mismatch', 'schema'), ('cli_worker_not_ready', 'not_ready'),
                         ('cli_path_forbidden', 'failed'), ('cli_permissions_or_bounds', 'failed'),
                         ('cli_receipt_invalid', 'failed')):
        if error.startswith(prefix): return code
    return 'unknown'


# Tail of every text prompt. agy does NOT pass --json-schema to the model, so the prompt itself carries the schema.
PROMPT_SUFFIX_HEAD = ("\n\nTrả lời bằng đúng MỘT JSON object khớp CHÍNH XÁC JSON Schema dưới đây: chỉ dùng các field có "
                      "trong schema, không thêm field nào khác, đúng kiểu và giới hạn. Không markdown, không giải thích. "
                      "Không cần và không được gọi tool.\nSchema: ")


def prompt_suffix(schema):
    return PROMPT_SUFFIX_HEAD + json.dumps(schema, ensure_ascii=False, separators=(',', ':'))


class AgyText:
    def __init__(self, config, *, clock=time.time, sleep=time.sleep):
        self.config, self.cfg, self.clock, self.sleep = config, config.subscription, clock, sleep
        self.last_receipt = None

    def ask(self, prompt, schema, *, purpose, deadline=None):
        """Return the model's JSON object. `deadline` (epoch) is the last moment a job may START.

        The same (purpose, prompt, schema) maps to the same job, so a restart reattaches to it
        instead of dispatching again. `unknown` and `complete*` jobs are final: to ask again after
        `unknown` the caller MUST change `purpose`. Only a job rejected BEFORE dispatch for a
        transient reason (daily cap of an earlier day, expired, cancelled, lock stopped) is retried
        automatically, as a new attempt job; nothing ran in the old one.
        Prompt and schema travel in the agy argv, so they are visible via ps/cmdline.
        """
        try:
            if isinstance(prompt, str) and prompt and isinstance(schema, dict):
                prompt += prompt_suffix(schema)
            too_big = (not isinstance(prompt, str) or not isinstance(schema, dict) or
                       text_argv_bytes(prompt, schema) > MAX_TEXT_ARGV_BYTES)
        except (TypeError, ValueError):
            too_big = True
        if too_big or not prompt or not isinstance(purpose, str) or not PURPOSE_RE.fullmatch(purpose):
            raise AgyTextError('invalid')
        try:
            cfg = self.cfg
            check_agent(cfg.text_agent)
            with Directory(cfg.queue_dir) as queue:
                job_id, attempt = self._pick(queue, purpose, prompt, schema)
                if not queue.exists(job_id):
                    try:
                        check_ready(cfg, self.clock, text=True)
                    except Exception:
                        raise AgyTextError('not_ready') from None
                    if deadline is not None and deadline <= self.clock():
                        raise AgyTextError('expired')
                    queue.mkdir(job_id)
                    self._write_request(queue, job_id, attempt, purpose, prompt, schema, deadline)
                else:
                    with Directory(job_id, parent=queue, owner=uid(cfg.producer_user)) as job:
                        missing = not job.exists('request.json')
                    if missing:
                        # Interrupted between mkdir and the request write: nothing dispatched; write it again.
                        self._write_request(queue, job_id, attempt, purpose, prompt, schema, deadline)
                with Directory(job_id, parent=queue, owner=uid(cfg.producer_user)) as job:
                    return self._wait(job, job_id)
        except ImageError as error:
            raise AgyTextError(classify(error), str(error)) from None
        except (ValueError, TypeError, OSError) as error:
            raise AgyTextError('failed', type(error).__name__) from None

    def _pick(self, queue, purpose, prompt, schema):
        """Attach to the existing job; advance only past a transient pre-dispatch rejection."""
        cfg = self.cfg
        today = datetime.fromtimestamp(self.clock(), VN_TZ).date().isoformat()
        for attempt in range(MAX_ATTEMPTS):
            job_id = text_job_id(purpose, prompt, schema, attempt)
            if not queue.exists(job_id): return job_id, attempt
            with Directory(job_id, parent=queue, owner=uid(cfg.producer_user)) as job:
                if not job.exists('receipt.json'): return job_id, attempt
                receipt = json.loads(job.read('receipt.json', owner=uid(cfg.worker_user)))
            error = receipt.get('error') if isinstance(receipt, dict) else None
            if (not isinstance(receipt, dict) or receipt.get('status') != 'rejected' or error not in TRANSIENT_REJECTIONS or
                    (error == 'cli_daily_job_cap' and receipt.get('day') == today)):
                return job_id, attempt
        raise AgyTextError('failed', 'too_many_attempts')

    def _write_request(self, queue, job_id, attempt, purpose, prompt, schema, deadline):
        cfg = self.cfg
        expires = deadline if deadline is not None else self.clock() + 2 * cfg.text_timeout_seconds
        with Directory(job_id, parent=queue, owner=uid(cfg.producer_user)) as job:
            job.atomic('request.json', {
                'protocol': TEXT_PROTOCOL, 'kind': 'text', 'job_id': job_id, 'purpose': purpose, 'prompt': prompt,
                'schema': schema, 'attempt': attempt, 'expires_at': expires, 'config': text_config_identity(cfg)})

    def _wait(self, job, job_id):
        cfg = self.cfg
        request = json.loads(job.read('request.json', owner=uid(cfg.producer_user)))
        validate_text_request(request, job_id, cfg)
        # A job may start just before expires_at and then run up to text_timeout_seconds.
        limit = request['expires_at'] + cfg.text_timeout_seconds + WAIT_MARGIN_SECONDS
        while not job.exists('receipt.json'):
            if self.clock() >= limit:
                if not job.exists('cancelled.json'): job.atomic('cancelled.json', {'cancelled': True})
                raise AgyTextError('unknown', 'no_receipt')
            self.sleep(POLL_SECONDS)
        receipt = json.loads(job.read('receipt.json', owner=uid(cfg.worker_user)))
        if (not isinstance(receipt, dict) or receipt.get('protocol') != TEXT_PROTOCOL or receipt.get('job_id') != job_id or
                receipt.get('request_sha') != digest(request)):
            raise AgyTextError('unknown', 'receipt_mismatch')
        if receipt.get('status') not in ('complete', 'complete_salvaged'):
            error = str(receipt.get('error', ''))
            code = classify(error)
            if code == 'unknown' and receipt.get('status') == 'rejected':
                code = 'failed'
            raise AgyTextError(code, error)
        worker = uid(cfg.worker_user)
        output = job.read('output.json', owner=worker, limit=2 * 1024 * 1024)
        try:
            transcript = job.read('native-transcript.json', owner=worker, limit=2 * 1024 * 1024)
        except ImageError:
            # Missing/unreadable proof follows the verifier's explicit receipt-invalid path, never a new job.
            transcript = None
        value = verify_text_receipt(cfg, receipt, request, output,
                                    job.read('native-final.bin', owner=worker), job.read('native-user.bin', owner=worker),
                                    transcript=transcript)
        from .text import check_schema
        if check_schema(request['schema'], value):
            raise AgyTextError('schema')
        self.last_receipt = receipt
        return value
