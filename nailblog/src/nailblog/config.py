"""Strict deployment configuration, fail-closed. API keys never live in TOML, only key-file paths."""

import math
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

from ._errors import ConfigError
from .schedule import MODES, TIMEZONE_NAME, parse_hhmm

DEFAULT_SLOTS = ("06:00", "19:00")
CATEGORIES = ("hot-trend", "nail-job", "nail-salon", "did-you-know")
DEFAULT_SLOT_CATEGORIES = {"06:00": ("hot-trend", "nail-job"), "19:00": ("nail-salon", "did-you-know")}
AGY_SLOTS = ("06:00", "10:00", "15:00", "19:00")   # the four daily slots of the Agy-only schedule
AGY_SLOT_CATEGORIES = {"06:00": ("hot-trend", "nail-job"), "10:00": ("did-you-know", "hot-trend"),
                       "15:00": ("nail-salon", "nail-job"), "19:00": ("nail-salon", "did-you-know")}
# Defaults applied when daily.slot_categories is absent and service.slots equals the keys of one of these maps.
SLOT_CATEGORY_DEFAULTS = (DEFAULT_SLOT_CATEGORIES, AGY_SLOT_CATEGORIES)
MAX_PUBLISH_PER_DAY = 4
GUID_PATTERN = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
API_ORIGIN = "https://api.nailhub.ai"
SITE_ORIGIN = "https://nailhub.ai"
STAGING_SITE_ORIGIN = "https://staging.nailhub.ai"
STAGING_API_ORIGIN = "https://api-staging.nailhub.ai"
STAGING_MEDIA_ORIGIN = "https://vlink-storages-test.sgp1.cdn.digitaloceanspaces.com"
PRODUCTION_MEDIA_ORIGIN = "https://vlink-storages-prod.sgp1.cdn.digitaloceanspaces.com"
TEXT_PROVIDERS = ("agy",)
THINKING_LEVELS = ("", "minimal", "low", "medium", "high")
IMAGE_PROVIDERS = ("subscription",)
# A name is refused as a secret when it contains one of these parts, is `key`, or ends with `key`
# (`provider_key`, `api_key`); a name ending `_file` is a path to a key file and is allowed.
SECRET_PARTS = ("token", "secret", "password", "passwd", "authorization", "api_key", "apikey")
TRENDS_HOST = "trends.google.com"
TRENDS_RSS_URL = "https://trends.google.com/trending/rss?geo=US"
TRENDS_API_IMPLEMENTED = False  # TrendsApiProvider (official Google Trends API, alpha) has no implementation yet.
DEFAULT_KEYWORDS = (
    "chrome nails", "cat eye nails", "velvet nails", "burgundy nails", "chocolate nails", "french tip nails",
    "halloween nails", "christmas nails", "thanksgiving nails", "winter nails", "glitter nails", "gel x",
    "builder gel", "nail art", "short nails", "almond nails", "square nails", "coffin nails", "aura nails",
    "jelly nails")
DEFAULT_ANCHORS = ("nail", "nails", "manicure", "pedicure", "mani", "pedi", "nail art", "nail salon", "nail polish",
                   "nail tech")
DEFAULT_CONTEXT = ("polish", "gel", "acrylic", "chrome", "french tip", "cat eye", "salon", "beauty", "makeup", "glitter")
MAX_TERMS = 60
SECTIONS = ("service", "telegram", "schedule", "text_model", "image", "budget", "contract", "console", "sources",
            "trends", "daily", "links", "admin", "autoflow", "subscription")
# Keys/tables dropped by the Agy-only migration: loading them fails with `removed_key:<path>`.
REMOVED_TABLES = ("cloudflare",)
REMOVED_KEYS = {
    "budget": ("daily_usd", "text_prices", "image_prices", "image_output_prices", "image_unit_prices"),
    "image": ("key_file", "key_source", "model_body", "model_cover", "review_model", "input_ceiling", "output_ceiling",
              "body_output_ceiling", "review_input_ceiling", "review_output_ceiling", "cover_size", "body_size"),
    "text_model": ("url", "key_file", "num_predict", "think", "section_thinking"),
    "autoflow": ("run_usd",),
}
KNOWN_KEYS = {
    "subscription": ("executable", "version", "agent_model", "effort", "queue_dir", "private_dir",
                     "worker_user", "producer_user", "timeout_seconds", "max_daily_jobs", "max_images_per_run",
                     "text_agent", "text_timeout_seconds"),
    "service": ("mode", "db_path", "owner_ids", "owner_username", "timezone", "slots", "late_window_minutes"),
    "telegram": ("session",),
    "schedule": ("image_mode", "fixed_at", "window_start_hours", "window_end_hours", "approve_deadline_hours"),
    "text_model": ("provider", "model", "timeout_seconds"),
    "image": ("provider", "candidates", "max_rounds", "timeout_seconds", "review_bypass"),
    "budget": ("ledger_db_path",),
    "contract": ("api_origin", "site_origin", "verified", "verification_ref", "environment", "media_origin"),
    "admin": ("credential_file", "profile_path", "timeout_seconds", "headless", "daily_publish_limit",
              "publish_timeout_seconds", "category_ids", "create_timeout_seconds"),
    "console": ("revision",),
    "sources": ("feeds", "timeout_seconds"),
    "trends": ("rss_url", "poll_hours", "keywords", "anchor_terms", "context_terms", "api_enabled"),
    "daily": ("propose_at", "pick_deadline", "preview_at", "slot_categories"),
    "autoflow": ("enabled", "runs_dir"),
    "links": ("jobs", "salon_sale", "app"),
}
MAX_FEEDS = 30


@dataclass(frozen=True)
class ScheduleConfig:
    image_mode: str = "fixed"
    fixed_at: str = "01:00"
    window_start_hours: int = 24
    window_end_hours: int = 3
    approve_deadline_hours: int = 2


@dataclass(frozen=True)
class TextModelConfig:
    provider: str = "agy"
    model: str = "qwen3.5:9b"
    timeout_seconds: int = 900


@dataclass(frozen=True)
class ImageConfig:
    provider: str = "subscription"
    candidates: int = 2
    max_rounds: int = 2
    timeout_seconds: int = 180
    review_bypass: bool = False   # TEMPORARY Owner decision 2026-10-22: skip model image review; see AGENTS.md


@dataclass(frozen=True)
class BudgetConfig:
    ledger_db_path: str = ""   # absolute; the retired API spend ledger, read-only (never written)


@dataclass(frozen=True)
class ContractConfig:
    api_origin: str = API_ORIGIN
    site_origin: str = SITE_ORIGIN
    verified: bool = False
    verification_ref: str = ""
    environment: str = "production"
    media_origin: str = ""


@dataclass(frozen=True)
class AdminConfig:
    credential_file: str = ""
    profile_path: str = ""
    timeout_seconds: int = 30
    headless: bool = True
    daily_publish_limit: int = 1
    publish_timeout_seconds: int = 300
    category_ids: dict = field(default_factory=dict)  # category slug -> GUID
    create_timeout_seconds: int = 90


@dataclass(frozen=True)
class SourcesConfig:
    feeds: tuple = ()
    timeout_seconds: int = 15


@dataclass(frozen=True)
class TrendsConfig:
    rss_url: str = TRENDS_RSS_URL
    poll_hours: int = 3
    keywords: tuple = DEFAULT_KEYWORDS
    anchor_terms: tuple = DEFAULT_ANCHORS
    context_terms: tuple = DEFAULT_CONTEXT
    api_enabled: bool = False


@dataclass(frozen=True)
class DailyConfig:
    propose_at: str = "10:00"
    pick_deadline: str = "13:00"
    preview_at: str = "20:00"
    slot_categories: dict = field(default_factory=lambda: dict(DEFAULT_SLOT_CATEGORIES))  # slot -> category slugs


@dataclass(frozen=True)
class AutoflowConfig:
    enabled: bool = False
    runs_dir: str = ""     # absolute; required once enabled


@dataclass(frozen=True)
class LinksConfig:
    """Internal CTA targets of an article. TODO Phase 0: the real NailHub URLs are not fixed yet, so every target
    points at the home page for now."""
    jobs: str = SITE_ORIGIN + "/"
    salon_sale: str = SITE_ORIGIN + "/"
    app: str = SITE_ORIGIN + "/"


@dataclass(frozen=True)
class SubscriptionConfig:
    executable: str = "/snap/bin/agy"
    version: str = "1.2.11"
    agent_model: str = "gemini-3.8-flash-medium"
    effort: str = "medium"
    queue_dir: str = ""
    private_dir: str = ""
    worker_user: str = "startechai"
    producer_user: str = "tgnailblog"
    timeout_seconds: int = 240
    max_daily_jobs: int = 48
    max_images_per_run: int = 12  # at most six positions and ONE explicit Owner rejection retry
    text_agent: str = "nailblog-text-only"   # agy agent policy used for text jobs
    text_timeout_seconds: int = 600


@dataclass(frozen=True)
class NailBlogConfig:
    db_path: str
    owner_ids: tuple = ()
    owner_username: str = "SotaThao"
    mode: str = "preview"
    timezone: str = TIMEZONE_NAME
    slots: tuple = DEFAULT_SLOTS
    late_window_minutes: int = 15
    telegram_session: str = "/var/lib/agentfleet/nailblog/nailblog.session"
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    text_model: TextModelConfig = field(default_factory=TextModelConfig)
    image: ImageConfig = field(default_factory=ImageConfig)
    budget: BudgetConfig = field(default_factory=BudgetConfig)
    contract: ContractConfig = field(default_factory=ContractConfig)
    console_revision: str = ""
    sources: SourcesConfig = field(default_factory=SourcesConfig)
    trends: TrendsConfig = field(default_factory=TrendsConfig)
    daily: DailyConfig = field(default_factory=DailyConfig)
    links: LinksConfig = field(default_factory=LinksConfig)
    admin: AdminConfig = field(default_factory=AdminConfig)
    autoflow: AutoflowConfig = field(default_factory=AutoflowConfig)
    subscription: SubscriptionConfig = field(default_factory=SubscriptionConfig)


def _fail(message):
    raise ConfigError(message)


def _table(data, name):
    value = data.get(name, {})
    if not isinstance(value, dict):
        _fail(f"{name} must be a TOML table")
    return value


def _int(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        _fail(f"{name} must be an integer in {low}..{high}")
    return value


def _number(value, name, low=0.0):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < low:
        _fail(f"{name} must be a finite number >= {low}")
    return float(value)


def _bool(value, name):
    if type(value) is not bool:
        _fail(f"{name} must be true or false")
    return value


def _string(value, name, *, empty=False):
    if not isinstance(value, str) or (not empty and not value.strip()):
        _fail(f"{name} must be a non-empty string" if not empty else f"{name} must be a string")
    return value


def _path(value, name, *, empty=False):
    _string(value, name, empty=empty)
    if value and not (Path(value).is_absolute() or PurePosixPath(value).is_absolute()):
        _fail(f"{name} must be an absolute path")
    return value


def _is_secret_name(name):
    folded = str(name).casefold()
    if folded.endswith("_file"):
        return False
    return folded == "key" or folded.endswith("key") or any(part in folded for part in SECRET_PARTS)


def _reject_secrets(node, trail=""):
    # A key named like a secret is refused wherever it appears, at any depth.
    if isinstance(node, dict):
        for name, value in node.items():
            if _is_secret_name(name):
                _fail(f"{trail}{name}: secrets must live in a key file, never in TOML")
            _reject_secrets(value, f"{trail}{name}.")
    elif isinstance(node, list):
        for item in node:
            _reject_secrets(item, trail)


def _only_known(table, section):
    for name in table:
        if name in REMOVED_KEYS.get(section, ()):
            _fail(f"removed_key:{section}.{name}")
        if name not in KNOWN_KEYS[section]:
            _fail(f"{section}.{name}: unknown configuration key")


def _origin(value, name, expected):
    parsed = urlparse(_string(value, name))
    if (parsed.scheme != "https" or parsed.hostname != urlparse(expected).hostname or parsed.username
            or parsed.password or parsed.port not in (None, 443) or parsed.path not in ("", "/")
            or parsed.query or parsed.fragment):
        _fail(f"{name} must be {expected}")
    return value.rstrip("/")


def _schedule(table):
    _only_known(table, "schedule")
    mode = table.get("image_mode", "fixed")
    if mode not in MODES:
        _fail("schedule.image_mode must be fixed, random or adaptive")
    fixed_at = _string(table.get("fixed_at", "01:00"), "schedule.fixed_at")
    try:
        parse_hhmm(fixed_at)
    except ValueError:
        _fail("schedule.fixed_at must be HH:MM")
    start = _int(table.get("window_start_hours", 24), "schedule.window_start_hours", 1, 72)
    end = _int(table.get("window_end_hours", 3), "schedule.window_end_hours", 1, 72)
    # 0 = approval allowed until the slot itself (staging immediate tests only; default stays 2).
    approve = _int(table.get("approve_deadline_hours", 2), "schedule.approve_deadline_hours", 0, 72)
    if not approve < end < start:
        _fail("schedule hours must satisfy approve_deadline < window_end < window_start")
    return ScheduleConfig(mode, fixed_at, start, end, approve)


def _text_model(table):
    _only_known(table, "text_model")
    if table.get("provider", "agy") != "agy":
        _fail("text_model.provider must be agy")
    model = _string(table.get("model", "qwen3.5:9b"), "text_model.model")
    return TextModelConfig("agy", model, timeout_seconds=_int(table.get("timeout_seconds", 900),
                                                              "text_model.timeout_seconds", 10, 3600))


def _image(table):
    _only_known(table, "image")
    if table.get("provider", "subscription") != "subscription":
        _fail("image.provider must be subscription")
    return ImageConfig("subscription",
                       candidates=_int(table.get("candidates", 2), "image.candidates", 1, 4),
                       max_rounds=_int(table.get("max_rounds", 2), "image.max_rounds", 1, 5),
                       timeout_seconds=_int(table.get("timeout_seconds", 180), "image.timeout_seconds", 10, 600),
                       review_bypass=_bool(table.get("review_bypass", False), "image.review_bypass"))


def _subscription(table):
    _only_known(table, "subscription")
    defaults = SubscriptionConfig()
    values = {name: _string(table.get(name, getattr(defaults, name)), "subscription." + name)
              for name in ("executable", "version", "agent_model", "effort", "worker_user", "producer_user")}
    if any(values[k] != getattr(defaults, k) for k in ("executable", "version", "agent_model", "effort")):
        _fail("subscription requires the reviewed agy 1.2.11 executable, model and effort")
    if any(not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", values[k]) for k in ("worker_user", "producer_user")):
        _fail("invalid subscription OS user")
    if values['worker_user'] == values['producer_user']:
        _fail("subscription worker and bot users must differ")
    return SubscriptionConfig(**values,
        queue_dir=_path(table.get('queue_dir', ''), 'subscription.queue_dir', empty=True),
        private_dir=_path(table.get('private_dir', ''), 'subscription.private_dir', empty=True),
        timeout_seconds=_int(table.get('timeout_seconds', 240), 'subscription.timeout_seconds', 30, 600),
        max_daily_jobs=_int(table.get('max_daily_jobs', 48), 'subscription.max_daily_jobs', 1, 48),
        text_agent=_string(table.get('text_agent', 'nailblog-text-only'), 'subscription.text_agent'),
        text_timeout_seconds=_int(table.get('text_timeout_seconds', 600), 'subscription.text_timeout_seconds', 120, 900),
        max_images_per_run=_int(table.get('max_images_per_run', 12), 'subscription.max_images_per_run', 4, 12))


def _budget(table):
    _only_known(table, "budget")
    return BudgetConfig(ledger_db_path=_path(table.get("ledger_db_path", ""), "budget.ledger_db_path", empty=True))


def _sources(table):
    _only_known(table, "sources")
    feeds = table.get("feeds", [])
    if not isinstance(feeds, list) or len(feeds) > MAX_FEEDS or len(set(map(str, feeds))) != len(feeds):
        _fail(f"sources.feeds must be a list of at most {MAX_FEEDS} unique https URLs")
    for url in feeds:
        parsed = urlparse(url) if isinstance(url, str) and 0 < len(url) <= 500 else None
        try:
            port = parsed.port if parsed else None
        except ValueError:
            parsed = None
        if (parsed is None or parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or port not in (None, 443) or parsed.fragment or any(ord(c) < 33 or ord(c) == 127 for c in url)):
            _fail("sources.feeds entries must be plain https URLs without credentials")
    return SourcesConfig(tuple(feeds), _int(table.get("timeout_seconds", 15), "sources.timeout_seconds", 1, 60))


def _terms(value, name, minimum=1):
    if not isinstance(value, list) or not minimum <= len(value) <= MAX_TERMS:
        _fail(f"{name} must be a list of {minimum}..{MAX_TERMS} words or phrases")
    seen = []
    for term in value:
        if (not isinstance(term, str) or not term.strip() or len(term) > 60 or not term.isprintable()
                or " ".join(term.split()).casefold() in seen):
            _fail(f"{name} entries must be unique printable strings of at most 60 characters")
        seen.append(" ".join(term.split()).casefold())
    return tuple(" ".join(term.split()) for term in value)


def _trends(table):
    if "relevance_terms" in table:
        _fail("trends.relevance_terms was replaced by trends.anchor_terms and trends.context_terms")
    _only_known(table, "trends")
    url = _string(table.get("rss_url", TRENDS_RSS_URL), "trends.rss_url")
    parsed = urlparse(url)
    try:
        port = parsed.port
    except ValueError:
        port = -1
    if (parsed.scheme != "https" or parsed.hostname != TRENDS_HOST or parsed.username or parsed.password
            or port not in (None, 443) or parsed.path != "/trending/rss" or parsed.query != "geo=US"
            or parsed.fragment):
        _fail("trends.rss_url must be " + TRENDS_RSS_URL + " (geo US only)")
    poll = _int(table.get("poll_hours", 3), "trends.poll_hours", 1, 24)
    keywords = _terms(table.get("keywords", list(DEFAULT_KEYWORDS)), "trends.keywords")
    anchors = _terms(table.get("anchor_terms", list(DEFAULT_ANCHORS)), "trends.anchor_terms")
    context = _terms(table.get("context_terms", list(DEFAULT_CONTEXT)), "trends.context_terms", minimum=0)
    api = _bool(table.get("api_enabled", False), "trends.api_enabled")
    if api and not TRENDS_API_IMPLEMENTED:
        _fail("trends.api_enabled=true needs a TrendsApiProvider implementation, which does not exist yet; "
              "keep it false (RSS frequency is used)")
    return TrendsConfig(TRENDS_RSS_URL, poll, keywords, anchors, context, api)


def _slot_categories(value, slots):
    if value is None:
        # Absent: the default groups, but only while they describe the configured slots (older configs keep loading).
        for defaults in SLOT_CATEGORY_DEFAULTS:
            if set(slots) == set(defaults):
                return dict(defaults)
        return {}
    if not isinstance(value, dict):
        _fail("daily.slot_categories must be a table of slot = [categories]")
    if set(value) != set(slots):
        _fail("daily.slot_categories keys must equal service.slots")
    groups = {}
    for slot, names in value.items():
        if (not isinstance(names, list) or not names or len(set(map(str, names))) != len(names)
                or any(name not in CATEGORIES for name in names)):
            _fail(f"daily.slot_categories.{slot} must be a non-empty list of unique categories: " + ", ".join(CATEGORIES))
        groups[slot] = tuple(names)
    return groups


def _daily(table, slots=DEFAULT_SLOTS):
    _only_known(table, "daily")
    times = {name: _string(table.get(name, default), "daily." + name)
             for name, default in (("propose_at", "10:00"), ("pick_deadline", "13:00"), ("preview_at", "20:00"))}
    for name, value in times.items():
        try:
            parse_hhmm(value)
        except ValueError:
            _fail(f"daily.{name} must be HH:MM")
    if not times["propose_at"] < times["pick_deadline"] < times["preview_at"]:
        _fail("daily times must satisfy propose_at < pick_deadline < preview_at")
    return DailyConfig(times["propose_at"], times["pick_deadline"], times["preview_at"],
                       _slot_categories(table.get("slot_categories"), slots))


def _category_ids(value):
    if not isinstance(value, dict):
        _fail("admin.category_ids must be a table of category = GUID")
    for slug, guid in value.items():
        if slug not in CATEGORIES:
            _fail(f"admin.category_ids.{slug}: unknown category (use {', '.join(CATEGORIES)})")
        if not isinstance(guid, str) or not GUID_PATTERN.fullmatch(guid):
            _fail(f"admin.category_ids.{slug} must be a GUID")
    return dict(value)


def _autoflow(table):
    _only_known(table, "autoflow")
    return AutoflowConfig(_bool(table.get("enabled", False), "autoflow.enabled"),
                          runs_dir=_path(table.get("runs_dir", ""), "autoflow.runs_dir", empty=True))


def _links(table, site_origin):
    _only_known(table, "links")
    host = urlparse(site_origin).hostname
    urls = {}
    for name in KNOWN_KEYS["links"]:
        url = _string(table.get(name, site_origin + "/"), "links." + name)
        parsed = urlparse(url)
        try:
            port = parsed.port
        except ValueError:
            port = -1
        if (parsed.scheme != "https" or parsed.hostname != host or parsed.username or parsed.password
                or port not in (None, 443) or parsed.fragment or len(url) > 300
                or any(ord(c) < 33 or ord(c) == 127 for c in url)):
            _fail(f"links.{name} must be an https URL on {host}")
        urls[name] = url
    return LinksConfig(**urls)


def load_config(path):
    path = Path(path)
    if not (path.is_absolute() or PurePosixPath(str(path)).is_absolute()):
        _fail("NAILBLOG_CONFIG must be an absolute TOML path")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
        _fail("NailBlog TOML configuration unreadable or invalid")
    _reject_secrets(data)
    for name in data:
        if name in REMOVED_TABLES:
            _fail(f"removed_key:{name}")
        if name not in SECTIONS:
            _fail(f"{name}: unknown configuration table")
    service, telegram = _table(data, "service"), _table(data, "telegram")
    _only_known(service, "service")
    _only_known(telegram, "telegram")
    db_path = _path(service.get("db_path", ""), "service.db_path")
    session = _path(telegram.get("session", "/var/lib/agentfleet/nailblog/nailblog.session"), "telegram.session")
    ids = service.get("owner_ids", [])
    if not isinstance(ids, list) or len(ids) != 1 or type(ids[0]) is not int or ids[0] <= 0:
        _fail("service.owner_ids must hold exactly one positive numeric Telegram ID")
    username = service.get("owner_username", "SotaThao")
    if not isinstance(username, str) or username != "SotaThao":
        _fail("service.owner_username must be SotaThao")
    mode = service.get("mode", "preview")
    if mode not in ("preview", "live"):
        _fail("service.mode must be preview or live")
    if service.get("timezone", TIMEZONE_NAME) != TIMEZONE_NAME:
        _fail("service.timezone must be " + TIMEZONE_NAME)
    slots = service.get("slots", list(DEFAULT_SLOTS))
    if not isinstance(slots, list) or not slots or len(set(slots)) != len(slots):
        _fail("service.slots must be a non-empty list of unique HH:MM values")
    for slot in slots:
        try:
            parse_hhmm(slot)
        except ValueError:
            _fail("service.slots must be a non-empty list of unique HH:MM values")
    late = _int(service.get("late_window_minutes", 15), "service.late_window_minutes", 1, 60)
    table = _table(data, "contract")
    _only_known(table, "contract")
    verified = _bool(table.get("verified", False), "contract.verified")
    reference = _string(table.get("verification_ref", ""), "contract.verification_ref", empty=True)
    if mode == "live" and (not verified or not reference.strip()):
        _fail("live mode needs contract.verified=true and a verification_ref")
    environment = table.get("environment", "production")
    if environment not in ("production", "staging"):
        _fail("contract.environment must be production or staging")
    site, api = ((STAGING_SITE_ORIGIN, STAGING_API_ORIGIN) if environment == "staging"
                 else (SITE_ORIGIN, API_ORIGIN))
    media_origin = _string(table.get('media_origin', ''), 'contract.media_origin', empty=True)
    if media_origin:
        parsed = urlparse(media_origin)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.netloc != parsed.hostname or
                parsed.path or parsed.params or parsed.query or parsed.fragment or
                (environment == 'staging' and media_origin != STAGING_MEDIA_ORIGIN) or
                (environment == 'production' and media_origin != PRODUCTION_MEDIA_ORIGIN)):
            _fail('contract.media_origin must be an observed HTTPS origin in the selected environment')
    contract = ContractConfig(_origin(table.get("api_origin", api), "contract.api_origin", api),
                              _origin(table.get("site_origin", site), "contract.site_origin", site),
                              verified, reference, environment, media_origin)
    admin = _table(data, "admin")
    _only_known(admin, "admin")
    admin = AdminConfig(_path(admin.get("credential_file", ""), "admin.credential_file", empty=True),
                        _path(admin.get("profile_path", ""), "admin.profile_path", empty=True),
                        _int(admin.get("timeout_seconds", 30), "admin.timeout_seconds", 5, 120),
                        _bool(admin.get("headless", True), "admin.headless"),
                        _int(admin.get("daily_publish_limit",1),"admin.daily_publish_limit",1,
                             min(len(slots), MAX_PUBLISH_PER_DAY)),
                        _int(admin.get("publish_timeout_seconds", 300), "admin.publish_timeout_seconds", 60, 900),
                        _category_ids(admin.get("category_ids", {})),
                        _int(admin.get("create_timeout_seconds", 90), "admin.create_timeout_seconds", 15, 300))
    console = _table(data, "console")
    _only_known(console, "console")
    image = _image(_table(data, "image"))
    budget = _budget(_table(data, "budget"))
    subscription = _subscription(_table(data, "subscription"))
    text_model = _text_model(_table(data, "text_model"))
    if mode == "live":
        # image.provider now defaults to subscription, so these gates bind a live run, not a preview load.
        if contract.environment != 'staging' or not image.review_bypass:
            _fail('subscription requires staging and the existing Owner review_bypass grant')
        if not subscription.queue_dir or not subscription.private_dir:
            _fail('subscription requires queue_dir and private_dir')
        if image.candidates != 1 or image.max_rounds != 1:
            _fail('subscription requires candidates = 1 and max_rounds = 1; no automatic retries')
    autoflow = _autoflow(_table(data, "autoflow"))
    if autoflow.enabled:
        if mode != "live":
            _fail("autoflow.enabled requires service.mode = live")
        if image.provider != "subscription" or text_model.provider != "agy":
            _fail('autoflow.enabled requires image.provider = "subscription" and text_model.provider = "agy"')
        if set(admin.category_ids) != set(CATEGORIES):
            _fail("autoflow.enabled requires admin.category_ids for all of " + ", ".join(CATEGORIES))
        if not autoflow.runs_dir:
            _fail("autoflow.enabled requires autoflow.runs_dir")
        if not budget.ledger_db_path:
            _fail("autoflow.enabled requires budget.ledger_db_path (read-only legacy spend ledger, absolute path)")
        if contract.environment != "staging":
            _fail("autoflow.enabled requires contract.environment = staging")
        if admin.daily_publish_limit < len(slots):
            _fail("autoflow.enabled requires admin.daily_publish_limit >= the number of service.slots")
    return NailBlogConfig(
        db_path, (ids[0],), username, mode, TIMEZONE_NAME, tuple(sorted(slots)), late, session,
        _schedule(_table(data, "schedule")), text_model,
        image, budget, contract,
        str(console.get("revision", "")), _sources(_table(data, "sources")), _trends(_table(data, "trends")),
        _daily(_table(data, "daily"), tuple(sorted(slots))), _links(_table(data, "links"), contract.site_origin), admin,
        autoflow, subscription)
