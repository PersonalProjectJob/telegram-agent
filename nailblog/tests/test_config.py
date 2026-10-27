import tempfile
import unittest
from pathlib import Path

from nailblog._errors import ConfigError
from nailblog.config import AGY_SLOT_CATEGORIES, NailBlogConfig, load_config
from tests.helpers import OWNER, load, write_config

ROOT = Path(__file__).resolve().parents[2]
LIVE_CONTRACT = 'verified = true\nverification_ref = "probe-2026-10-20"'
LIVE_IMAGE = 'candidates = 1\nmax_rounds = 1\nreview_bypass = true'
LIVE_SUBSCRIPTION = 'queue_dir = "/var/lib/nailblog-subscription/queue"\nprivate_dir = "/home/w/private"'
LIVE_CONTRACT_STAGING = LIVE_CONTRACT + '\nenvironment = "staging"'


class ConfigTests(unittest.TestCase):
    def invalid(self, pattern, service=None, **sections):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ConfigError, pattern):
                load(directory, service, **sections)

    def test_minimal_config_gets_documented_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            config = load(directory)
        self.assertIsInstance(config, NailBlogConfig)
        self.assertEqual((config.mode, config.owner_username, config.owner_ids), ("preview", "SotaThao", (OWNER,)))
        self.assertEqual((config.timezone, config.slots, config.late_window_minutes),
                         ("Asia/Ho_Chi_Minh", ("06:00", "19:00"), 15))
        self.assertEqual((config.image.provider, config.text_model.provider), ("subscription", "agy"))
        self.assertEqual((config.subscription.text_agent, config.subscription.text_timeout_seconds,
                          config.subscription.max_daily_jobs, config.admin.create_timeout_seconds),
                         ("nailblog-text-only", 600, 48, 90))
        self.assertEqual((config.schedule.window_start_hours, config.schedule.window_end_hours,
                          config.schedule.approve_deadline_hours), (24, 3, 2))
        self.assertEqual((config.text_model.provider, config.text_model.model), ("agy", "qwen3.5:9b"))
        self.assertEqual((config.image.candidates, config.image.max_rounds), (2, 2))
        self.assertIs(config.image.review_bypass, False)
        self.assertEqual((config.contract.api_origin, config.contract.site_origin, config.contract.verified),
                         ("https://api.nailhub.ai", "https://nailhub.ai", False))

    def test_full_agy_config_parses(self):
        with tempfile.TemporaryDirectory() as directory:
            config = load(directory, None,
                          text_model='provider = "agy"\nmodel = "m"\ntimeout_seconds = 300',
                          image='provider = "subscription"\ncandidates = 3\nmax_rounds = 1',
                          budget='ledger_db_path = "/var/lib/agentfleet/nailblog/ledger.sqlite3"',
                          subscription='text_agent = "my-agent"\ntext_timeout_seconds = 120\nmax_daily_jobs = 10',
                          admin='create_timeout_seconds = 300', console='revision = "abc"')
        self.assertEqual((config.text_model.provider, config.text_model.model, config.text_model.timeout_seconds),
                         ("agy", "m", 300))
        self.assertEqual((config.image.provider, config.image.candidates), ("subscription", 3))
        self.assertEqual(config.budget.ledger_db_path, "/var/lib/agentfleet/nailblog/ledger.sqlite3")
        self.assertEqual((config.subscription.text_agent, config.subscription.text_timeout_seconds,
                          config.subscription.max_daily_jobs, config.admin.create_timeout_seconds, config.console_revision),
                         ("my-agent", 120, 10, 300, "abc"))

    def test_live_requires_verified_contract_and_reference(self):
        live = dict(image=LIVE_IMAGE, subscription=LIVE_SUBSCRIPTION)
        self.invalid("live mode needs", {"mode": "live"}, **live)
        self.invalid("live mode needs", {"mode": "live"}, contract="verified = true", **live)
        self.invalid("live mode needs", {"mode": "live"}, contract='verified = true\nverification_ref = "  "', **live)
        self.invalid("live mode needs", {"mode": "live"}, contract='verified = false\nverification_ref = "x"', **live)
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(load(directory, {"mode": "live"}, contract=LIVE_CONTRACT_STAGING, **live).mode, "live")

    def test_live_subscription_gates(self):
        self.invalid("requires staging and the existing Owner review_bypass", {"mode": "live"},
                     contract=LIVE_CONTRACT, image=LIVE_IMAGE, subscription=LIVE_SUBSCRIPTION)
        self.invalid("requires staging and the existing Owner review_bypass", {"mode": "live"},
                     contract=LIVE_CONTRACT_STAGING, image="candidates = 1\nmax_rounds = 1", subscription=LIVE_SUBSCRIPTION)
        self.invalid("requires queue_dir and private_dir", {"mode": "live"}, contract=LIVE_CONTRACT_STAGING, image=LIVE_IMAGE)
        self.invalid("candidates = 1 and max_rounds = 1", {"mode": "live"}, contract=LIVE_CONTRACT_STAGING,
                     image="review_bypass = true", subscription=LIVE_SUBSCRIPTION)

    def test_mode_must_be_preview_or_live(self):
        self.invalid("service.mode", {"mode": "dry"})

    def test_paths_must_be_absolute(self):
        self.invalid("service.db_path must be an absolute", {"db_path": "relative/db.sqlite3"})
        self.invalid("service.db_path must be a non-empty", {"db_path": None})
        self.invalid("telegram.session must be an absolute", telegram='session = "nailblog.session"')
        self.invalid("budget.ledger_db_path must be an absolute", budget='ledger_db_path = "ledger.sqlite3"')
        self.invalid("subscription.queue_dir must be an absolute", subscription='queue_dir = "queue"')
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ConfigError, "absolute TOML path"):
                load_config("relative.toml")

    def test_unreadable_or_malformed_toml_is_a_config_error(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ConfigError, "unreadable or invalid"):
                load_config(Path(directory) / "missing.toml")
            broken = Path(directory) / "broken.toml"
            broken.write_text("[service\n", encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "unreadable or invalid"):
                load_config(broken)

    def test_owner_binding_is_exactly_one_numeric_id_for_sotathao(self):
        for ids in ([], [1, 2], ["1603321775"], [True], [0], [-5], "1603321775"):
            with self.subTest(ids=ids):
                self.invalid("owner_ids", {"owner_ids": ids})
        self.invalid("owner_ids", {"owner_ids": None})
        for name in ("SomeoneElse", "sotathao", 5):
            with self.subTest(name=name):
                self.invalid("owner_username", {"owner_username": name})

    def test_timezone_is_pinned(self):
        self.invalid("service.timezone", {"timezone": "America/New_York"})

    def test_slots_validation(self):
        for slots in ([], ["06:00", "06:00"], ["6:00"], ["25:00"], ["06:60"], "06:00", [600]):
            with self.subTest(slots=slots):
                self.invalid("service.slots", {"slots": slots})
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(load(directory, {"slots": ["19:00", "06:00"]}).slots, ("06:00", "19:00"))

    def test_late_window_bounds(self):
        for value in (0, -1, 61, "15", True, 1.5):
            with self.subTest(value=value):
                self.invalid("late_window_minutes", {"late_window_minutes": value})

    def test_schedule_validation(self):
        self.invalid("image_mode", schedule='image_mode = "weekly"')
        self.invalid("fixed_at", schedule='fixed_at = "1am"')
        self.invalid("fixed_at", schedule="fixed_at = 100")
        self.invalid("window_start_hours", schedule='window_start_hours = "24"')
        self.invalid("schedule hours must satisfy", schedule="window_end_hours = 30")
        self.invalid("schedule hours must satisfy", schedule="approve_deadline_hours = 3")
        with tempfile.TemporaryDirectory() as directory:
            for mode in ("fixed", "random", "adaptive"):
                self.assertEqual(load(directory, None, schedule=f'image_mode = "{mode}"').schedule.image_mode, mode)

    def test_text_model_provider_is_agy_only(self):
        for provider in ("ollama", "deepseek", "gemini", "openai"):
            with self.subTest(provider=provider):
                self.invalid("text_model.provider must be agy", text_model=f'provider = "{provider}"')
        self.invalid("text_model.model", text_model='model = ""')
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(load(directory, None, text_model='provider = "agy"').text_model.provider, "agy")

    def test_image_validation(self):
        for provider in ("gemini", "openai", "cloudflare", "stability"):
            with self.subTest(provider=provider):
                self.invalid("image.provider must be subscription", image=f'provider = "{provider}"')
        self.invalid("image.candidates", image="candidates = 0")
        self.invalid("image.review_bypass", image='review_bypass = "yes"')
        self.invalid("image.candidates", image="candidates = 9")
        self.invalid("image.max_rounds", image="max_rounds = 0")

    def test_removed_keys_and_tables_fail_with_their_path(self):
        removed = {
            "text_model": ("url", "key_file", "num_predict", "think", "section_thinking"),
            "image": ("key_file", "key_source", "model_body", "model_cover", "review_model", "input_ceiling",
                      "output_ceiling", "body_output_ceiling", "review_input_ceiling", "review_output_ceiling",
                      "cover_size", "body_size"),
            "budget": ("daily_usd", "text_prices", "image_prices", "image_output_prices", "image_unit_prices"),
            "autoflow": ("run_usd",)}
        for section, names in removed.items():
            for name in names:
                with self.subTest(key=f"{section}.{name}"):
                    self.invalid(f"^removed_key:{section}[.]{name}$", **{section: f"{name} = 1"})
        self.invalid("^removed_key:cloudflare$", cloudflare='account_id = ""')
        self.invalid("^removed_key:cloudflare$", cloudflare="")

    def test_legacy_config_fields_are_gone_from_the_dataclasses(self):
        from dataclasses import fields
        from nailblog import config as module
        names = lambda cls: {f.name for f in fields(cls)}   # noqa: E731
        self.assertEqual(names(module.TextModelConfig), {"provider", "model", "timeout_seconds"})
        self.assertEqual(names(module.ImageConfig), {"provider", "candidates", "max_rounds", "timeout_seconds",
                                                      "review_bypass"})
        self.assertEqual(names(module.BudgetConfig), {"ledger_db_path"})
        self.assertEqual(names(module.AutoflowConfig), {"enabled", "runs_dir"})
        self.assertNotIn("cloudflare", names(module.NailBlogConfig))
        self.assertFalse(hasattr(module, "CloudflareConfig"))
        for gone in ("KEY_SOURCES", "UNIT_PRICE_SIZES"):
            self.assertFalse(hasattr(module, gone), gone)
        self.assertEqual(module.TEXT_PROVIDERS, ("agy",))

    def test_subscription_text_keys_bounds(self):
        for body in ("text_timeout_seconds = 119", "text_timeout_seconds = 901", 'text_timeout_seconds = "600"',
                     "max_daily_jobs = 49", "max_daily_jobs = 0"):
            with self.subTest(body=body):
                self.invalid("subscription[.]", subscription=body)
        self.invalid("subscription.text_agent must be a non-empty string", subscription='text_agent = ""')
        with tempfile.TemporaryDirectory() as directory:
            for value in (120, 900):
                config = load(directory, None, subscription=f"text_timeout_seconds = {value}")
                self.assertEqual(config.subscription.text_timeout_seconds, value)

    def test_admin_create_timeout_bounds(self):
        for body in ("14", "301", '"90"', "true"):
            with self.subTest(body=body):
                self.invalid("admin.create_timeout_seconds must be an integer in 15..300",
                             admin=f"create_timeout_seconds = {body}")
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(load(directory, None, admin="create_timeout_seconds = 15").admin.create_timeout_seconds, 15)

    def test_budget_keeps_only_the_ledger_path(self):
        with tempfile.TemporaryDirectory() as directory:
            config = load(directory, None, budget='ledger_db_path = "/var/lib/agentfleet/nailblog/ledger.sqlite3"')
        self.assertEqual(config.budget.ledger_db_path, "/var/lib/agentfleet/nailblog/ledger.sqlite3")
        self.invalid("budget.surprise: unknown configuration key", budget="surprise = 1")

    def test_contract_origins_are_pinned(self):
        for origin in ("https://evil.example", "http://api.nailhub.ai", "https://api.nailhub.ai/v1",
                       "https://user@api.nailhub.ai", "https://api.nailhub.ai:8443"):
            with self.subTest(origin=origin):
                self.invalid("contract.api_origin", contract=f'api_origin = "{origin}"')
        self.invalid("contract.site_origin", contract='site_origin = "https://nailhub.ai.evil.example"')
        self.invalid("contract.verified", contract='verified = "yes"')

    def test_secrets_in_toml_are_refused_wherever_they_appear(self):
        self.invalid("never in TOML", text_model='key = "sk-123"')
        self.invalid("never in TOML", image='api_key = "abc"')
        self.invalid("never in TOML", subscription='agy_token = "abc"')
        self.invalid("never in TOML", budget='token = "abc"')
        self.invalid("never in TOML", {"password": "pw"})
        self.invalid("never in TOML", x='nested = { secret = "s" }')

    def test_example_template_matches_the_loader_and_fails_closed_until_owner_id_is_set(self):
        text = (ROOT / "config" / "nailblog.example.toml").read_text(encoding="utf-8")
        self.assertIn('mode = "preview"', text)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "example.toml"
            path.write_text(text.replace('"/var/lib/agentfleet/nailblog/', f'"{Path(directory).as_posix()}/'), encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "owner_ids"):
                load_config(path)
            path.write_text(text.replace("owner_ids = []", f"owner_ids = [{OWNER}]"), encoding="utf-8")
            config = load_config(path)
        self.assertEqual((config.mode, config.owner_ids, config.slots),
                         ("preview", (OWNER,), ("06:00", "10:00", "15:00", "19:00")))
        self.assertEqual((config.text_model.provider, config.image.provider), ("agy", "subscription"))
        self.assertEqual((config.subscription.text_agent, config.subscription.text_timeout_seconds,
                          config.subscription.max_daily_jobs), ("nailblog-text-only", 600, 48))
        self.assertEqual(config.daily.slot_categories, AGY_SLOT_CATEGORIES)
        active = "".join(line for line in text.splitlines(True) if not line.lstrip().startswith("#"))
        for removed in ("cloudflare", "daily_usd", "run_usd", "key_source", "model_body"):
            self.assertNotIn(removed, active)
        self.assertIn("review_bypass = false", text)
        self.assertIs(config.image.review_bypass, False)


if __name__ == "__main__":
    unittest.main()
