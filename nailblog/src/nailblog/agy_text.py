"""Producer side of agy text jobs (protocol nailblog-agy-text-v1).

The bot submits a job into the subscription queue and waits for the worker's
receipt. It NEVER holds MODEL_LOCK while waiting: the worker takes the shared
lock itself, so a bot-held lock would deadlock. A job that ended `unknown` is
final; callers decide whether to ask again with a different `purpose`.
"""
import json
import time

from .images import ImageError
from .images.subscription import (Directory, TEXT_PROTOCOL, PURPOSE_RE, check_agent,
                                  check_ready, text_config_identity, text_job_id, uid, validate_text_request,
                                  verify_text_receipt)
from .publication import digest

MAX_PROMPT = 60000
POLL_SECONDS = 1
WAIT_MARGIN_SECONDS = 30


class AgyTextError(Exception):
    """`code`: unknown | not_ready | held | daily_cap | expired | cancelled | invalid | schema | failed."""

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


class AgyText:
    def __init__(self, config, *, clock=time.time, sleep=time.sleep):
        self.config, self.cfg, self.clock, self.sleep = config, config.subscription, clock, sleep
        self.last_receipt = None

    def ask(self, prompt, schema, *, purpose, deadline=None):
        """Return the model's JSON object. `deadline` (epoch) is the last moment a job may START.

        The same (purpose, prompt, schema) always maps to the same job, so a restart
        reattaches to it instead of dispatching again.
        """
        if (not isinstance(prompt, str) or not 1 <= len(prompt) <= MAX_PROMPT or not isinstance(schema, dict) or
                not isinstance(purpose, str) or not PURPOSE_RE.fullmatch(purpose)):
            raise AgyTextError('invalid')
        try:
            cfg = self.cfg
            check_agent(cfg.text_agent)
            job_id = text_job_id(purpose, prompt, schema)
            with Directory(cfg.queue_dir) as queue:
                if not queue.exists(job_id):
                    try:
                        check_ready(cfg, self.clock, text=True)
                    except Exception:
                        raise AgyTextError('not_ready') from None
                    now = self.clock()
                    expires = deadline if deadline is not None else now + 2 * cfg.text_timeout_seconds
                    if expires <= now:
                        raise AgyTextError('expired')
                    queue.mkdir(job_id)
                    with Directory(job_id, parent=queue, owner=uid(cfg.producer_user)) as job:
                        job.atomic('request.json', {
                            'protocol': TEXT_PROTOCOL, 'kind': 'text', 'job_id': job_id, 'purpose': purpose,
                            'prompt': prompt, 'schema': schema, 'expires_at': expires,
                            'config': text_config_identity(cfg)})
                with Directory(job_id, parent=queue, owner=uid(cfg.producer_user)) as job:
                    return self._wait(job, job_id)
        except ImageError as error:
            raise AgyTextError(classify(error)) from None
        except (ValueError, TypeError, OSError) as error:
            raise AgyTextError('failed', type(error).__name__) from None

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
            code = classify(receipt.get('error'))
            if code == 'unknown' and receipt.get('status') == 'rejected':
                code = 'failed'
            raise AgyTextError(code, str(receipt.get('error', '')))
        worker = uid(cfg.worker_user)
        output = job.read('output.json', owner=worker, limit=2 * 1024 * 1024)
        value = verify_text_receipt(cfg, receipt, request, output,
                                    job.read('native-final.bin', owner=worker), job.read('native-user.bin', owner=worker))
        from .text import check_schema
        if check_schema(request['schema'], value):
            raise AgyTextError('schema')
        self.last_receipt = receipt
        return value
