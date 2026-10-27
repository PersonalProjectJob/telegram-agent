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
KEY_SOURCES = ("file", "env")
UNIT_PRICE_SIZES = ("1K", "2K", "4K")
MAX_PUBLISH_PER_DAY = 3
GUID_PATTERN = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
API_ORIGIN = "https://api.nailhub.ai"
SITE_ORIGIN = "https://nailhub.ai"
STAGING_SITE_ORIGIN = "https://staging.nailhub.ai"
STAGING_API_ORIGIN = "https://api-staging.nailhub.ai"
STAGING_MEDIA_ORIGIN = "https://vlink-storages-test.sgp1.cdn.digitaloceanspaces.com"
PRODUCTION_MEDIA_ORIGIN = "https://vlink-storages-prod.sgp1.cdn.digitaloceanspaces.com"
TEXT_PROVIDERS = ("ollama", "deepseek", "gemini")
THINKING_LEVELS = ("", "minimal", "low", "medium", "high")
IMAGE_PROVIDERS = ("gemini", "openai", "cloudflare", "subscription")
# A name is refused as a secret when it contains one of these parts, is `key`, or ends with `key`
# (`deepseek_key`, `api_key`); a name ending `_file` is a path to a key file and is allowed.
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
            "trends", "daily", "links", "admin", "cloudflare", "autoflow", "subscription")
KNOWN_KEYS = {
    "subscription": ("executable", "version", "agent_model", "effort", "queue_dir", "private_dir",
                     "worker_user", "producer_user", "timeout_seconds", "max_daily_jobs", "max_images_per_run"),
    "service": ("mode", "db_path", "owner_ids", "owner_username", "timezone", "slots", "late_window_minutes"),
    "telegram": ("session",),
    "schedule": ("image_mode", "fixed_at", "window_start_hours", "window_end_hours", "approve_deadline_hours"),
    "text_model": ("provider", "model", "url", "think", "key_file", "num_predict", "timeout_seconds",
                   "section_thinking"),
    "image": ("provider", "model_body", "model_cover", "review_model", "key_file", "candidates", "max_rounds",
              "cover_size", "body_size", "input_ceiling", "output_ceiling", "body_output_ceiling", "review_input_ceiling",
              "review_output_ceiling", "timeout_seconds", "review_bypass", "key_source"),
    "budget": ("daily_usd", "text_prices", "image_prices", "image_output_prices", "image_unit_prices",
               "ledger_db_path"),
    "contract": ("api_origin", "site_origin", "verified", "verification_ref", "environment", "media_origin"),
    "admin": ("credential_file", "profile_path", "timeout_seconds", "headless", "daily_publish_limit",
              "publish_timeout_seconds", "category_ids"),
    "console": ("revision",),
    "sources": ("feeds", "timeout_seconds"),
    "trends": ("rss_url", "poll_hours", "keywords", "anchor_terms", "context_terms", "api_enabled"),
    "daily": ("propose_at", "pick_deadline", "preview_at", "slot_categories"),
    "autoflow": ("enabled", "run_usd", "runs_dir"),
    "links": ("jobs", "salon_sale", "app"),
    "cloudflare": ("account_id", "free_proof_file", "quota_db_path", "daily_neurons", "review_input_allowance",
                   "reference_image_file", "reference_image_sha256", "two_article_free_day"),
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
    provider: str = "ollama"
    model: str = "qwen3.5:9b"
    url: str = "http://127.0.0.1:11434"
    think: bool = False
    key_file: str = ""
    num_predict: int = 3000
    timeout_seconds: int = 900
    section_thinking: str = "low"   # gemini only: thinking level of the section/repair calls ("" = provider default)


@dataclass(frozen=True)
class ImageConfig:
    provider: str = "gemini"
    model_body: str = ""
    model_cover: str = ""
    review_model: str = ""
    key_file: str = ""
    candidates: int = 2
    max_rounds: int = 2
    cover_size: str = "2K"
    body_size: str = "1K"
    input_ceiling: int = 4096
    output_ceiling: int = 2048
    review_input_ceiling: int = 16384
    review_output_ceiling: int = 1024
    timeout_seconds: int = 180
    body_output_ceiling: int = 1536
    review_bypass: bool = False   # TEMPORARY Owner decision 2026-10-22: skip model image review; see AGENTS.md
    key_source: str = "file"      # "env": the key comes from env_secrets (captured at start), no key_file needed


@dataclass(frozen=True)
class BudgetConfig:
    daily_usd: float = 3.0
    text_prices: dict = field(default_factory=dict)   # model -> (usd per 1M input tokens, usd per 1M output tokens)
    image_prices: dict = field(default_factory=dict)  # model -> usd per image
    image_output_prices: dict = field(default_factory=dict)  # model -> upper USD/1M output including images/thinking
    image_unit_prices: dict = field(default_factory=dict)  # model -> {"1K": usd, "2K": usd} per generated image
    ledger_db_path: str = ""   # absolute; when set, EVERY paid ledger (pipeline, compare, one-post, text) uses this DB


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


@dataclass(frozen=True)
class CloudflareConfig:
    account_id: str = ""
    free_proof_file: str = ""
    quota_db_path: str = ""
    daily_neurons: float = 10000.0
    review_input_allowance: int = 32768
    reference_image_file: str = ""
    reference_image_sha256: str = ""
    two_article_free_day: bool = False


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
    run_usd: float = 0.0   # 0.0 = unset; required (> 0, <= budget.daily_usd) once enabled
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
    max_daily_jobs: int = 24
    max_images_per_run: int = 12  # at most six positions and ONE explicit Owner rejection retry


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
    cloudflare: CloudflareConfig = field(default_factory=CloudflareConfig)
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
    provider = table.get("provider", "ollama")
    if provider not in TEXT_PROVIDERS:
        _fail("text_model.provider must be ollama, deepseek or gemini")
    if table.get("think", False) is not False:
        _fail("text_model.think must be false (Owner-approved no-thinking mode)")
    if provider in ("deepseek", "gemini") and "model" not in table:
        _fail(f"text_model.model is required for {provider}")
    model = _string(table.get("model", "qwen3.5:9b"), "text_model.model")
    url = _string(table.get("url", "http://127.0.0.1:11434"), "text_model.url")
    local = urlparse(url)
    if (local.scheme != "http" or local.hostname not in ("127.0.0.1", "localhost", "::1") or local.username
            or local.password or local.path not in ("", "/") or local.query or local.fragment):
        _fail("text_model.url must be a plain-http localhost Ollama URL")
    key_file = _path(table.get("key_file", ""), "text_model.key_file", empty=True)
    if provider in ("deepseek", "gemini") and not key_file:
        _fail(f"text_model.key_file is required for {provider}")
    thinking = table.get("section_thinking", "low")
    if thinking not in THINKING_LEVELS:
        _fail("text_model.section_thinking must be empty, minimal, low, medium or high")
    return TextModelConfig(provider, model, url.rstrip("/"), False, key_file,
                           _int(table.get("num_predict", 3000), "text_model.num_predict", 64, 8192),
                           _int(table.get("timeout_seconds", 900), "text_model.timeout_seconds", 10, 3600), thinking)


def _image(table, live):
    _only_known(table, "image")
    provider = table.get("provider", "gemini")
    if provider not in IMAGE_PROVIDERS:
        _fail("image.provider must be gemini, openai, cloudflare or subscription")
    models = {name: _string(table.get(name, ""), "image." + name, empty=True)
              for name in ("model_body", "model_cover", "review_model")}
    key_file = _path(table.get("key_file", ""), "image.key_file", empty=True)
    key_source = table.get("key_source", "file")
    if key_source not in KEY_SOURCES:
        _fail("image.key_source must be file or env")
    if live and provider != "subscription" and (not all(models.values()) or (key_source == "file" and not key_file)):
        _fail("live mode requires image models and image.key_file" if key_source == "file"
              else "live mode requires image models")
    sizes = [table.get(name, default) for name, default in (("cover_size", "2K"), ("body_size", "1K"))]
    if any(size not in ("1K", "2K", "4K") for size in sizes):
        _fail("image sizes must be 1K, 2K or 4K")
    return ImageConfig(provider, models["model_body"], models["model_cover"], models["review_model"], key_file,
                       _int(table.get("candidates", 2), "image.candidates", 1, 4),
                       _int(table.get("max_rounds", 2), "image.max_rounds", 1, 5), *sizes,
                       _int(table.get("input_ceiling",4096),"image.input_ceiling",64,65536),
                       _int(table.get("output_ceiling",2048),"image.output_ceiling",256,32768),
                       _int(table.get("review_input_ceiling",16384),"image.review_input_ceiling",512,65536),
                       _int(table.get("review_output_ceiling",1024),"image.review_output_ceiling",256,8192),
                       _int(table.get("timeout_seconds",180),"image.timeout_seconds",10,600),
                       _int(table.get("body_output_ceiling",1536),"image.body_output_ceiling",256,32768),
                       _bool(table.get("review_bypass", False), "image.review_bypass"), key_source)


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
        max_daily_jobs=_int(table.get('max_daily_jobs', 24), 'subscription.max_daily_jobs', 1, 48),
        max_images_per_run=_int(table.get('max_images_per_run', 12), 'subscription.max_images_per_run', 4, 12))


def _budget(table):
    _only_known(table, "budget")
    text_prices, image_prices = {}, {}
    for model, price in _table(table, "text_prices").items():
        if not isinstance(price, dict) or set(price) != {"input_per_m", "output_per_m"}:
            _fail(f"budget.text_prices.{model} needs input_per_m and output_per_m")
        text_prices[model] = (_number(price["input_per_m"], f"budget.text_prices.{model}.input_per_m"),
                              _number(price["output_per_m"], f"budget.text_prices.{model}.output_per_m"))
    for model, price in _table(table, "image_prices").items():
        image_prices[model] = _number(price, f"budget.image_prices.{model}")
    output_prices = {model:_number(price, 'budget.image_output_prices.'+model, 0.000001)
                     for model,price in _table(table,'image_output_prices').items()}
    unit_prices = {}
    for model, sizes in _table(table, "image_unit_prices").items():
        if not isinstance(sizes, dict) or not sizes or not set(sizes) <= set(UNIT_PRICE_SIZES):
            _fail(f"budget.image_unit_prices.{model} must be a table of {' and/or '.join(UNIT_PRICE_SIZES)} prices")
        unit_prices[model] = {size: _number(price, f"budget.image_unit_prices.{model}.{size}", 0.000001)
                              for size, price in sizes.items()}
    return BudgetConfig(_number(table.get("daily_usd", 3.0), "budget.daily_usd", 0.0), text_prices, image_prices,
                        output_prices, unit_prices,
                        _path(table.get("ledger_db_path", ""), "budget.ledger_db_path", empty=True))


def _cloudflare(table):
    import re
    _only_known(table,'cloudflare')
    account = _string(table.get('account_id',''),'cloudflare.account_id',empty=True)
    if account and not re.fullmatch(r'[a-f0-9]{32}',account):
        _fail('cloudflare.account_id must be 32 lowercase hexadecimal characters')
    cap = _number(table.get('daily_neurons',10000.0),'cloudflare.daily_neurons',0.001)
    if cap > 10000: _fail('cloudflare.daily_neurons must not exceed Free allocation 10000')
    reference=_path(table.get('reference_image_file',''),'cloudflare.reference_image_file',empty=True)
    reference_sha=_string(table.get('reference_image_sha256',''),'cloudflare.reference_image_sha256',empty=True)
    if bool(reference)!=bool(reference_sha) or (reference_sha and not re.fullmatch(r'[a-f0-9]{64}',reference_sha)):
        _fail('cloudflare reference image requires an absolute path and lowercase SHA256 together')
    return CloudflareConfig(account,_path(table.get('free_proof_file',''),'cloudflare.free_proof_file',empty=True),
        _path(table.get('quota_db_path',''),'cloudflare.quota_db_path',empty=True),cap,
        _int(table.get('review_input_allowance',32768),'cloudflare.review_input_allowance',1024,65536),reference,reference_sha,
        _bool(table.get('two_article_free_day',False),'cloudflare.two_article_free_day'))


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
        return dict(DEFAULT_SLOT_CATEGORIES) if set(slots) == set(DEFAULT_SLOT_CATEGORIES) else {}
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
                          _number(table.get("run_usd", 0.0), "autoflow.run_usd", 0.0),
                          _path(table.get("runs_dir", ""), "autoflow.runs_dir", empty=True))


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
                        _category_ids(admin.get("category_ids", {})))
    console = _table(data, "console")
    _only_known(console, "console")
    image = _image(_table(data, "image"), mode == "live")
    budget = _budget(_table(data, "budget"))
    subscription = _subscription(_table(data, "subscription"))
    if image.provider == 'subscription':
        if contract.environment != 'staging' or not image.review_bypass:
            _fail('subscription requires staging and the existing Owner review_bypass grant')
        if not subscription.queue_dir or not subscription.private_dir:
            _fail('subscription requires queue_dir and private_dir')
        if image.candidates != 1 or image.max_rounds != 1:
            _fail('subscription requires candidates = 1 and max_rounds = 1; no automatic retries')
        if image.key_file or image.key_source != 'file':
            _fail('subscription must not configure an API key source')
    autoflow = _autoflow(_table(data, "autoflow"))
    if autoflow.run_usd > budget.daily_usd:
        _fail("autoflow.run_usd must be > 0 and <= budget.daily_usd")
    if autoflow.enabled:
        if mode != "live":
            _fail("autoflow.enabled requires service.mode = live")
        if image.provider != 'subscription' and (image.provider != "gemini" or image.key_source != "env"):
            _fail('autoflow.enabled requires image.provider = "gemini" and image.key_source = "env"')
        if set(admin.category_ids) != set(CATEGORIES):
            _fail("autoflow.enabled requires admin.category_ids for all of " + ", ".join(CATEGORIES))
        for model, size in (() if image.provider == 'subscription' else ((image.model_cover, image.cover_size), (image.model_body, image.body_size))):
            if size not in budget.image_unit_prices.get(model, {}):
                _fail(f"autoflow.enabled requires a {size} price for {model} in budget.image_unit_prices")
        if not 0 < autoflow.run_usd <= budget.daily_usd:
            _fail("autoflow.run_usd must be > 0 and <= budget.daily_usd")
        if not autoflow.runs_dir:
            _fail("autoflow.enabled requires autoflow.runs_dir")
        if not budget.ledger_db_path:
            _fail("autoflow.enabled requires budget.ledger_db_path (one shared daily cap across state roots)")
        if contract.environment != "staging":
            _fail("autoflow.enabled requires contract.environment = staging")
        if admin.daily_publish_limit < len(slots):
            _fail("autoflow.enabled requires admin.daily_publish_limit >= the number of service.slots")
    return NailBlogConfig(
        db_path, (ids[0],), username, mode, TIMEZONE_NAME, tuple(sorted(slots)), late, session,
        _schedule(_table(data, "schedule")), _text_model(_table(data, "text_model")),
        image, budget, contract,
        str(console.get("revision", "")), _sources(_table(data, "sources")), _trends(_table(data, "trends")),
        _daily(_table(data, "daily"), tuple(sorted(slots))), _links(_table(data, "links"), contract.site_origin), admin,
        _cloudflare(_table(data,'cloudflare')), autoflow, subscription)
