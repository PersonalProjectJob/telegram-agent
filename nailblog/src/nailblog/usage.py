"""API cost accounting (TD-6): per-call rows, a daily USD ceiling, and the console summary file.

Only counts and cost are stored, never prompts, content or keys. The summary file
(`agentfleet-usage.json`, schema 1) follows the listener's camelCase contract, written atomically
next to the database, at most once per `interval` seconds. Days are Asia/Ho_Chi_Minh calendar days.
"""

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from .schedule import VN_TZ

LOG = logging.getLogger(__name__)

SUMMARY_NAME = "agentfleet-usage.json"
SUMMARY_SCHEMA = 1
SUMMARY_INTERVAL_SECONDS = 30.0
DAILY_DAYS = 30
RETENTION_DAYS = 90


class BudgetExceeded(RuntimeError):
    """Today's spend has reached budget.daily_usd; no new paid call may start."""


class UnpricedModel(ValueError):
    """A paid model has no entry in the [budget] price tables (fail-closed)."""


def day_bounds(ts):
    """[start, end) epoch seconds of the Vietnam calendar day containing `ts`."""
    day = datetime.fromtimestamp(ts, VN_TZ).date()
    start = datetime(day.year, day.month, day.day, tzinfo=VN_TZ)
    return start.timestamp(), (start + timedelta(days=1)).timestamp()


def text_cost(budget, provider, model, tokens_in, tokens_out):
    if provider in ("ollama", "agy"):   # local model / Agy subscription: no API bill
        return 0.0
    if model not in budget.text_prices:
        raise UnpricedModel("no text price for model")
    price_in, price_out = budget.text_prices[model]
    return (max(0, tokens_in) * price_in + max(0, tokens_out) * price_out) / 1e6


def image_cost(budget, model, count, provider=None):
    if "subscription" in (provider, model):   # Agy subscription route: no API bill
        return 0.0
    if model not in budget.image_prices:
        raise UnpricedModel("no image price for model")
    return max(0, count) * budget.image_prices[model]


def _window(rows, since, image_models=frozenset()):
    models, total = {}, {"calls": 0, "ok": 0, "errors": 0, "fallbacks": 0, "promptTokens": 0,
                         "completionTokens": 0, "cacheHitTokens": 0, "images": 0, "cost": 0.0}
    for at, provider, model, units, usd in rows:
        if at < since:
            continue
        entry = models.setdefault((provider, model), {
            "provider": provider, "model": model, "calls": 0, "errors": 0, "fallbacks": 0,
            "promptTokens": 0, "completionTokens": 0, "cacheHitTokens": 0, "images": 0, "cost": 0.0})
        # units = tokens (text; no in/out split stored) or images; image units must not inflate token counts.
        key = "images" if model in image_models else "promptTokens"
        for target in (entry, total):
            target["calls"] += 1
            target[key] += int(units)
            target["cost"] += usd
    total["ok"] = total["calls"]
    for target in (total, *models.values()):
        target["cost"] = round(target["cost"], 6)
    total["byModel"] = sorted(models.values(), key=lambda e: (-e["calls"], e["provider"], e["model"]))
    return total


def build_summary(rows, now, currency="USD", image_models=frozenset()):
    today = datetime.fromtimestamp(now, VN_TZ).date()
    days = [today - timedelta(days=offset) for offset in range(DAILY_DAYS - 1, -1, -1)]
    daily = {day: {"date": day.isoformat(), "calls": 0, "errors": 0, "fallbacks": 0, "cost": 0.0} for day in days}
    for at, _provider, _model, _units, usd in rows:
        line = daily.get(datetime.fromtimestamp(at, VN_TZ).date())
        if line is not None:
            line["calls"] += 1
            line["cost"] += usd
    for line in daily.values():
        line["cost"] = round(line["cost"], 6)

    def start_of(day):
        return datetime(day.year, day.month, day.day, tzinfo=VN_TZ).timestamp()
    return {"schema": SUMMARY_SCHEMA, "generatedAt": float(now), "currency": currency,
            "windows": {"today": _window(rows, start_of(today), image_models),
                        "7d": _window(rows, start_of(today - timedelta(days=6)), image_models),
                        "30d": _window(rows, start_of(days[0]), image_models)},
            "daily": [daily[day] for day in days]}


def write_atomic(path, payload):
    path = Path(path)
    temp = path.with_suffix(".tmp")
    try:
        with temp.open("w", encoding="utf-8") as stream:
            if os.name != "nt":
                import grp
                try:
                    os.chown(temp, -1, grp.getgrnam("tgnailblog").gr_gid)
                except (KeyError, OSError):
                    pass
            os.chmod(temp, 0o640 if os.name != "nt" else 0o600)
            json.dump(payload, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    except BaseException:
        try:
            temp.unlink()
        except OSError:
            pass
        raise


class UsageRecorder:
    def __init__(self, state, config, *, clock=time.time, interval=SUMMARY_INTERVAL_SECONDS):
        self.state = state
        self.config = config
        self.budget = config.budget
        self.clock = clock
        self.interval = interval
        self.path = Path(config.db_path).parent / SUMMARY_NAME
        self._lock = threading.Lock()
        self._last_write = None

    def _legacy_path(self):
        """The retired API ledger when `budget.ledger_db_path` is set (and is not this DB), else ''."""
        shared = getattr(self.budget, "ledger_db_path", "")
        return "" if not shared or str(shared) == str(self.state.path) else str(shared)

    def _legacy_rows(self, start, end):
        """Usage rows of the retired ledger, read through a private snapshot (the ledger is never opened for write)."""
        from .paid import usage_rows
        return usage_rows(self._legacy_path(), start, end)

    def total_today(self):
        start, end = day_bounds(self.clock())
        return self.state.usd_between(start, end) + sum(row[4] for row in self._legacy_rows(start, end))

    def check(self):
        """Call before every paid request: refuse once today's total reaches the ceiling."""
        from .paid import PaidLedger
        PaidLedger(self.state, self.config, self.clock).check()

    def record(self, provider, model, units, usd):
        self.state.add_usage(self.clock(), provider, model, units, usd)
        self.publish()

    def record_text(self, provider, model, tokens_in, tokens_out):
        usd = text_cost(self.budget, provider, model, tokens_in, tokens_out)
        self.record(provider, model, max(0, tokens_in) + max(0, tokens_out), usd)
        return usd

    def record_image(self, provider, model, count):
        usd = image_cost(self.budget, model, count)
        self.record(provider, model, count, usd)
        return usd

    def publish(self, *, force=False):
        """Write the summary now or at most once per interval; never raises."""
        with self._lock:
            now = self.clock()
            if not force and self._last_write is not None and now - self._last_write < self.interval:
                return False
            try:
                start = day_bounds(now)[0] - (DAILY_DAYS - 1) * 86400
                rows = self.state.usage_between(start, now + 1)
                rows = sorted(rows + self._legacy_rows(start, now + 1))   # old API spend booked in the retired ledger
                write_atomic(self.path, build_summary(rows, now,
                                                      image_models=frozenset(self.budget.image_prices) | frozenset(self.budget.image_output_prices)))
            except Exception as error:
                LOG.warning("Usage summary write failed exception=%s", type(error).__name__)
                return False
            self._last_write = now
            return True

    def prune(self):
        return self.state.prune_usage(self.clock() - RETENTION_DAYS * 86400)
