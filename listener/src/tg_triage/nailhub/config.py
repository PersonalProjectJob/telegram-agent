"""Strict deployment configuration. No browser or credentials are loaded on import."""

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from ..config import ConfigError


@dataclass(frozen=True)
class NailHubConfig:
    db_path: str
    profile_path: str
    owner_ids: tuple = ()
    mode: str = "preview"
    interval_seconds: int = 600
    max_batch: int = 1
    url: str = "https://nailhub.ai/admin/postings"
    ollama_url: str = "http://127.0.0.1:11434"
    model: str = "qwen3.5:9b"
    timeout_seconds: int = 600
    verified: bool = False
    verification_ref: str = ""
    selectors: dict = field(default_factory=dict)
    fields: dict = field(default_factory=dict)
    options: dict = field(default_factory=dict)
    draft_status: str = "Draft"
    approved_status: str = "Approved"
    category: str = "JobPosting"
    salary_range: bool = False
    salary_places: int = 2
    headless: bool = True
    credential_file: str = ""
    api_origin: str = "https://api.nailhub.ai"
    max_pages: int = 50
    range_policy: str = 'upper_floor_100'
    owner_username: str = 'SotaThao'
    num_predict: int = 2048


def load_optional(env=None):
    env = os.environ if env is None else env
    value = env.get("NAILHUB_CONFIG", "").strip()
    return load_config(value) if value else None


def load_config(path):
    path = Path(path)
    if not path.is_absolute():
        raise ConfigError("NAILHUB_CONFIG must be an absolute TOML path")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        worker, model, contract = data["worker"], data.get("model", {}), data.get("contract", {})
        db, profile = worker["db_path"], worker["profile_path"]
        if not all(isinstance(p, str) and Path(p).is_absolute() for p in (db, profile)):
            raise ValueError()
        ids = worker.get("owner_ids", [])
        if not isinstance(ids, list) or any(type(i) is not int or i <= 0 for i in ids):
            raise ValueError()
        username = worker.get('owner_username', 'SotaThao')
        if not isinstance(username, str) or username.casefold() != 'sotathao' or len(ids) > 1:
            raise ConfigError('NailHub Owner binding must be @SotaThao and one numeric ID')
        mode = worker.get("mode", "preview")
        interval, batch = worker.get("interval_seconds", 600), worker.get("max_batch", 1)
        timeout = model.get("timeout_seconds", 600)
        num_predict = model.get('num_predict', 2048)
        places = contract.get("salary_places", 2)
        if (mode not in ("preview", "live") or type(interval) is not int or interval < 10
                or type(batch) is not int or not 1 <= batch <= 20
                or type(timeout) is not int or not 1 <= timeout <= 1800
                or type(num_predict) is not int or not 256 <= num_predict <= 8192
                or type(places) is not int or not 0 <= places <= 2):
            raise ValueError()
        if model.get("model", "qwen3.5:9b") != "qwen3.5:9b" or model.get("think", False) is not False:
            raise ConfigError("NailHub requires qwen3.5:9b with Owner-approved think=false")
        url = worker.get("url", "https://nailhub.ai/admin/postings")
        target = urlparse(url)
        ollama = model.get("url", "http://127.0.0.1:11434")
        local = urlparse(ollama)
        if (target.scheme != "https" or target.hostname != "nailhub.ai" or target.username
                or target.password or local.scheme != "http" or local.hostname not in ("127.0.0.1", "localhost", "::1")
                or local.username or local.password or local.path not in ("", "/")):
            raise ValueError()
        selectors, fields, options = (data.get(k, {}) for k in ("selectors", "fields", "options"))
        if any(not isinstance(d, dict) or any(not isinstance(k, str) or not isinstance(v, str) or not v.strip()
                                            for k, v in d.items()) for d in (selectors, fields, options)):
            raise ValueError()
        verified = contract.get("verified", False)
        headless, salary_range = worker.get("headless", True), contract.get("salary_range", False)
        if any(type(v) is not bool for v in (verified, headless, salary_range)):
            raise ValueError()
        reference = contract.get("verification_ref", "")
        if not isinstance(reference, str):
            raise ValueError()
        if mode == "live" and (not verified or not reference.strip() or not ids):
            raise ConfigError("Live NailHub needs VERIFIED contract, evidence reference and numeric Owner IDs")
        credential_file = worker.get('credential_file', '')
        if not isinstance(credential_file, str) or (credential_file and not Path(credential_file).is_absolute()):
            raise ValueError()
        api_origin = contract.get('api_origin', 'https://api.nailhub.ai')
        api = urlparse(api_origin)
        if api.scheme != 'https' or api.hostname != 'api.nailhub.ai' or api.username or api.password or api.path not in ('', '/'):
            raise ValueError()
        pages = worker.get('max_pages', 50)
        if type(pages) is not int or not 1 <= pages <= 500:
            raise ValueError()
        range_policy = worker.get('range_policy', 'upper_floor_100')
        if range_policy not in ('hold', 'upper_floor_100'):
            raise ValueError()
        if (contract.get('category', 'JobPosting') != 'JobPosting'
                or contract.get('draft_status', 'Draft') != 'Draft'
                or contract.get('approved_status', 'Approved') != 'Approved'):
            raise ConfigError('Only JobPosting Draft -> Approved contract is supported')
        return NailHubConfig(db, profile, tuple(ids), mode, interval, batch, url,
                             ollama.rstrip("/"), "qwen3.5:9b", timeout, verified, reference,
                             selectors, fields, options, contract.get("draft_status", "Draft"),
                             contract.get("approved_status", "Approved"), contract.get("category", "JobPosting"),
                             salary_range, places, headless, credential_file, api_origin.rstrip('/'), pages, range_policy, username, num_predict)
    except ConfigError:
        raise
    except (OSError, ValueError, TypeError, KeyError):
        raise ConfigError("Invalid NailHub TOML configuration") from None
