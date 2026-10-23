"""Autoflow shared contracts: new config keys loaded through the real TOML loader, and env_secrets."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from nailblog import env_secrets, gemini_text
from nailblog._errors import ConfigError
from nailblog.config import (CATEGORIES, DEFAULT_SLOT_CATEGORIES, AdminConfig, AutoflowConfig, BudgetConfig,
                             DailyConfig, ImageConfig, _is_secret_name, load_config)
from tests.helpers import load

ROOT = Path(__file__).resolve().parents[2]
GUID = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"
LIVE = {"mode": "live"}
CONTRACT = 'verified = true\nverification_ref = "report-1"'
IMAGE_ENV = 'provider = "gemini"\nmodel_body = "m-body"\nmodel_cover = "m-cover"\nreview_model = "m-review"\nkey_source = "env"'
IMAGE_FILE = 'provider = "gemini"\nmodel_body = "m-body"\nmodel_cover = "m-cover"\nreview_model = "m-review"\nkey_file = "/etc/nailblog/k.json"'
IDS = "\n".join(f'{slug} = "{GUID}"' for slug in CATEGORIES)


def live_sections(**extra):
    """A complete live-gemini-env configuration; keyword args replace or add raw TOML tables."""
    sections = {"contract": CONTRACT, "image": IMAGE_ENV, "budget": "daily_usd = 3.0",
                "budget__image_unit_prices__m-cover": '"2K" = 0.134', "budget__image_unit_prices__m-body": '"1K" = 0.039',
                "admin__category_ids": IDS,
                "autoflow": 'enabled = true\nrun_usd = 1.0\nruns_dir = "/var/lib/agentfleet/nailblog/runs"'}
    sections.update(extra)
    return {name: body for name, body in sections.items() if body is not None}


class ConfigCase(unittest.TestCase):
    def load(self, service=None, **sections):
        with tempfile.TemporaryDirectory() as directory:
            return load(directory, service, **sections)

    def invalid(self, pattern, service=None, **sections):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ConfigError, pattern):
                load(directory, service, **sections)


class DefaultsTests(ConfigCase):
    def test_defaults_are_unchanged(self):
        config = self.load()
        self.assertEqual(config.image.key_source, "file")
        self.assertEqual(config.budget.image_unit_prices, {})
        self.assertEqual(config.admin, AdminConfig())
        self.assertEqual(config.admin.publish_timeout_seconds, 300)
        self.assertEqual(config.admin.category_ids, {})
        self.assertEqual(config.admin.daily_publish_limit, 1)
        self.assertEqual(config.daily, DailyConfig())
        self.assertEqual(config.daily.slot_categories,
                         {"06:00": ("hot-trend", "nail-job"), "19:00": ("nail-salon", "did-you-know")})
        self.assertEqual(config.autoflow, AutoflowConfig(enabled=False))
        self.assertEqual(ImageConfig().key_source, "file")
        self.assertEqual(BudgetConfig().image_unit_prices, {})

    def test_the_shipped_example_still_loads_with_defaults(self):
        text = (ROOT / "config" / "nailblog.example.toml").read_text(encoding="utf-8").replace("owner_ids = []", "owner_ids = [1603321775]")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nailblog.toml"
            path.write_text(text, encoding="utf-8")
            config = load_config(path)
        self.assertFalse(config.autoflow.enabled)
        self.assertEqual(config.image.key_source, "file")
        self.assertEqual(config.daily.slot_categories, DEFAULT_SLOT_CATEGORIES)

    def test_file_key_live_mode_still_needs_a_key_file(self):
        self.invalid("live mode requires image models and image.key_file", LIVE, contract=CONTRACT,
                     image='provider = "openai"\nmodel_body = "a"\nmodel_cover = "b"\nreview_model = "c"')

    def test_env_key_live_mode_needs_no_key_file(self):
        config = self.load(LIVE, **live_sections(autoflow=None, admin__category_ids=None))
        self.assertEqual(config.image.key_source, "env")
        self.assertEqual(config.image.key_file, "")
        self.assertFalse(config.autoflow.enabled)

    def test_bad_key_source_is_rejected(self):
        self.invalid("image.key_source must be file or env", image='key_source = "vault"')


class ImageUnitPriceTests(ConfigCase):
    def test_live_gemini_without_prices_loads_as_before(self):
        config = self.load(LIVE, **live_sections(autoflow=None, admin__category_ids=None,
                                                 budget="daily_usd = 3.0", **{"budget__image_unit_prices__m-cover": None,
                                                                              "budget__image_unit_prices__m-body": None}))
        self.assertEqual(config.budget.image_unit_prices, {})

    def test_live_gemini_with_prices_loads_and_keeps_them(self):
        config = self.load(LIVE, **live_sections(autoflow=None, admin__category_ids=None))
        self.assertEqual(config.budget.image_unit_prices, {"m-cover": {"2K": 0.134}, "m-body": {"1K": 0.039}})

    def test_autoflow_without_a_cover_price_fails_closed(self):
        self.invalid("autoflow.enabled requires a 2K price for m-cover", LIVE,
                     **live_sections(**{"budget__image_unit_prices__m-cover": None}))

    def test_autoflow_without_the_body_size_price_fails_closed(self):
        self.invalid("autoflow.enabled requires a 1K price for m-body", LIVE,
                     **live_sections(**{"budget__image_unit_prices__m-body": '"2K" = 0.07'}))

    def test_autoflow_price_must_match_the_configured_size(self):
        self.invalid("autoflow.enabled requires a 1K price for m-cover", LIVE,
                     **live_sections(image=IMAGE_ENV + '\ncover_size = "1K"'))

    def test_a_4k_price_is_accepted(self):
        config = self.load(budget__image_unit_prices__m='"4K" = 0.24')
        self.assertEqual(config.budget.image_unit_prices, {"m": {"4K": 0.24}})

    def test_preview_mode_does_not_need_prices(self):
        self.assertEqual(self.load(image=IMAGE_ENV).budget.image_unit_prices, {})

    def test_malformed_prices_are_rejected(self):
        self.invalid("image_unit_prices.m must be a table", budget__image_unit_prices="m = 0.1")
        self.invalid("image_unit_prices.m must be a table", budget__image_unit_prices__m='"8K" = 0.1')
        self.invalid("image_unit_prices.m.1K must be a finite number", budget__image_unit_prices__m='"1K" = 0')
        self.invalid("image_unit_prices.m.1K must be a finite number", budget__image_unit_prices__m='"1K" = "x"')


class AdminTests(ConfigCase):
    def test_category_ids_and_timeout_load(self):
        config = self.load(admin=f"publish_timeout_seconds = 900\n[admin.category_ids]\n{IDS}")
        self.assertEqual(config.admin.category_ids, {slug: GUID for slug in CATEGORIES})
        self.assertEqual(config.admin.publish_timeout_seconds, 900)

    def test_timeout_bounds(self):
        self.assertEqual(self.load(admin="publish_timeout_seconds = 60").admin.publish_timeout_seconds, 60)
        for value in (59, 901, "300", True):
            self.invalid("admin.publish_timeout_seconds must be an integer in 60..900",
                         admin="publish_timeout_seconds = " + {True: 'true', '300': '"300"'}.get(value, str(value)))

    def test_unknown_slug_and_bad_guid_are_rejected(self):
        self.invalid("admin.category_ids.food: unknown category", admin__category_ids=f'food = "{GUID}"')
        self.invalid("admin.category_ids.hot-trend must be a GUID", admin__category_ids='hot-trend = "123"')
        self.invalid("admin.category_ids must be a table", admin='category_ids = "x"')

    def test_publish_limit_follows_the_slot_count(self):
        self.assertEqual(self.load(admin="daily_publish_limit = 2").admin.daily_publish_limit, 2)
        self.invalid("admin.daily_publish_limit must be an integer in 1..2", admin="daily_publish_limit = 3")
        self.invalid("admin.daily_publish_limit must be an integer in 1..2", admin="daily_publish_limit = 0")

    def test_three_slots_allow_three_but_never_four(self):
        slots = {"slots": ["06:00", "12:00", "19:00"]}
        groups = '"06:00" = ["hot-trend"]\n"12:00" = ["nail-job"]\n"19:00" = ["nail-salon", "did-you-know"]'
        self.assertEqual(self.load(slots, admin="daily_publish_limit = 3", daily__slot_categories=groups)
                         .admin.daily_publish_limit, 3)
        slots = {"slots": ["06:00", "10:00", "12:00", "19:00"]}
        groups = '"06:00" = ["hot-trend"]\n"10:00" = ["nail-job"]\n"12:00" = ["nail-salon"]\n"19:00" = ["did-you-know"]'
        self.invalid("1..3", slots, admin="daily_publish_limit = 4", daily__slot_categories=groups)


class DailyTests(ConfigCase):
    def test_explicit_slot_categories_load_as_tuples(self):
        config = self.load(daily='[daily.slot_categories]\n"06:00" = ["nail-job"]\n"19:00" = ["hot-trend", "did-you-know"]')
        self.assertEqual(config.daily.slot_categories, {"06:00": ("nail-job",), "19:00": ("hot-trend", "did-you-know")})

    def test_keys_must_equal_the_service_slots(self):
        self.invalid("daily.slot_categories keys must equal service.slots", daily__slot_categories='"06:00" = ["hot-trend"]')
        self.invalid("daily.slot_categories keys must equal service.slots", {"slots": ["06:00"]},
                     daily__slot_categories='"06:00" = ["hot-trend"]\n"19:00" = ["nail-job"]')

    def test_groups_are_validated(self):
        base = '"06:00" = {}\n"19:00" = ["nail-salon"]'
        for bad in ("[]", '["blog"]', '["hot-trend", "hot-trend"]', '"hot-trend"'):
            self.invalid("daily.slot_categories.06:00 must be", daily__slot_categories=base.format(bad))

    def test_custom_slots_without_the_table_keep_loading(self):
        self.assertEqual(self.load({"slots": ["07:00"]}).daily.slot_categories, {})


class AutoflowTests(ConfigCase):
    def test_a_complete_enabled_configuration_loads(self):
        config = self.load(LIVE, **live_sections())
        self.assertEqual(config.autoflow, AutoflowConfig(True, 1.0, "/var/lib/agentfleet/nailblog/runs"))

    def test_each_requirement_fails_closed(self):
        self.invalid("autoflow.enabled requires service.mode = live", None, **live_sections())
        self.invalid("requires image.provider = \"gemini\" and image.key_source = \"env\"", LIVE,
                     **live_sections(image=IMAGE_FILE))
        self.invalid("requires image.provider = \"gemini\" and image.key_source = \"env\"", LIVE,
                     **live_sections(image=IMAGE_ENV.replace("gemini", "openai"), budget__image_unit_prices__m_cover=None))
        self.invalid("autoflow.enabled requires admin.category_ids for all of", LIVE,
                     **live_sections(admin__category_ids=IDS.splitlines()[0]))
        self.invalid("autoflow.enabled requires admin.category_ids for all of", LIVE, **live_sections(admin__category_ids=None))
        self.invalid("autoflow.enabled requires autoflow.runs_dir", LIVE, **live_sections(autoflow="enabled = true\nrun_usd = 1.0"))

    def test_run_usd_bounds(self):
        for body in ("enabled = true\nrun_usd = 0\n", "enabled = true\nrun_usd = 3.5\n", "enabled = true\nrun_usd = -1\n"):
            self.invalid("autoflow.run_usd", LIVE, **live_sections(autoflow=body + 'runs_dir = "/r"'))
        self.invalid("autoflow.run_usd must be > 0", LIVE, **live_sections(autoflow='enabled = true\nruns_dir = "/r"'))
        self.invalid("autoflow.run_usd must be > 0 and <= budget.daily_usd", autoflow="run_usd = 4.0")
        self.assertEqual(self.load(LIVE, **live_sections(autoflow='enabled = true\nrun_usd = 3.0\nruns_dir = "/r"')).autoflow.run_usd, 3.0)

    def test_runs_dir_must_be_absolute(self):
        self.invalid("autoflow.runs_dir must be an absolute path", autoflow='runs_dir = "runs"')

    def test_disabled_autoflow_asks_for_nothing(self):
        self.assertEqual(self.load(autoflow="enabled = false").autoflow, AutoflowConfig())

    def test_enabled_must_be_a_boolean(self):
        self.invalid("autoflow.enabled must be true or false", autoflow='enabled = "yes"')


class SchemaStrictnessTests(ConfigCase):
    def test_unknown_keys_are_rejected(self):
        for section in ("autoflow", "admin", "daily", "image", "budget"):
            self.invalid(f"{section}.surprise: unknown configuration key", **{section: "surprise = 1"})

    def test_secret_looking_names_are_rejected(self):
        for section, line in (("autoflow", "run_key = 1"), ("autoflow", "api_token = 1"), ("image", 'gemini_key = "x"'),
                              ("admin", 'password = "x"'), ("daily", 'my_secret = 1')):
            self.invalid("secrets must live in a key file", **{section: line})

    def test_the_new_names_are_not_secret_names(self):
        for name in ("key_source", "image_unit_prices", "category_ids", "publish_timeout_seconds", "slot_categories",
                     "enabled", "run_usd", "runs_dir"):
            self.assertFalse(_is_secret_name(name), name)

    def test_errors_never_carry_a_key_value(self):
        marker = "AIzaSy-test-marker-value"
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ConfigError) as caught:
                load(directory, LIVE, **live_sections(image=IMAGE_ENV + f'\nkey_value = "{marker}"'))
        self.assertNotIn(marker, str(caught.exception))


class EnvSecretsTests(unittest.TestCase):
    KEY = "AIzaSy-unit-test-placeholder"

    def setUp(self):
        self.addCleanup(setattr, env_secrets, "_captured", env_secrets._captured)
        env_secrets._captured = ""

    def test_constants_match_the_contract(self):
        self.assertEqual(env_secrets.GEMINI_IMAGE_ENV, "NAILBLOG_GEMINI_IMAGE_KEY")

    def test_capture_removes_the_variable_and_keeps_the_value(self):
        environ = {env_secrets.GEMINI_IMAGE_ENV: self.KEY, "OTHER": "kept"}
        env_secrets.capture(environ)
        self.assertEqual(environ, {"OTHER": "kept"})
        self.assertEqual(env_secrets.gemini_key_reader("", gemini_text.KEY_SCOPE), self.KEY)

    def test_capture_is_idempotent(self):
        environ = {env_secrets.GEMINI_IMAGE_ENV: self.KEY}
        env_secrets.capture(environ)
        env_secrets.capture(environ)
        env_secrets.capture({})
        self.assertEqual(env_secrets.gemini_key_reader("", gemini_text.KEY_SCOPE), self.KEY)

    def test_capture_defaults_to_the_process_environment(self):
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, {env_secrets.GEMINI_IMAGE_ENV: self.KEY}):
            env_secrets.capture()
            self.assertNotIn(env_secrets.GEMINI_IMAGE_ENV, os.environ)
        self.assertEqual(env_secrets.gemini_key_reader("", gemini_text.KEY_SCOPE), self.KEY)

    def test_reader_gives_nothing_for_other_scopes_or_before_capture(self):
        self.assertEqual(env_secrets.gemini_key_reader("", gemini_text.KEY_SCOPE), "")
        env_secrets.capture({env_secrets.GEMINI_IMAGE_ENV: self.KEY})
        for scope in ("", "cloudflare|https://api.cloudflare.com|acct", "deepseek|https://api.deepseek.com", None):
            self.assertEqual(env_secrets.gemini_key_reader("/some/key.json", scope), "")

    def test_reader_is_call_compatible_with_read_key_callers(self):
        env_secrets.capture({env_secrets.GEMINI_IMAGE_ENV: self.KEY})
        self.assertEqual(env_secrets.gemini_key_reader("/p", gemini_text.KEY_SCOPE, group_name="tgnailblog"), self.KEY)
        self.assertEqual(env_secrets.gemini_key_reader("/p", gemini_text.KEY_SCOPE, "tgnailblog"), self.KEY)

    def test_nothing_is_logged_or_printed(self):
        out, err = io.StringIO(), io.StringIO()
        with self.assertLogs(level="DEBUG") as logs, contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            logging_probe = __import__("logging").getLogger("nailblog.probe")
            logging_probe.info("probe")  # assertLogs needs at least one record
            env_secrets.capture({env_secrets.GEMINI_IMAGE_ENV: self.KEY})
            env_secrets.gemini_key_reader("/p", gemini_text.KEY_SCOPE)
            env_secrets.gemini_key_reader("/p", "other")
        self.assertNotIn(self.KEY, "\n".join(logs.output) + out.getvalue() + err.getvalue())
        self.assertEqual(out.getvalue() + err.getvalue(), "")

    def test_main_captures_right_after_argument_parsing(self):
        import inspect
        from nailblog import __main__ as entry
        source = inspect.getsource(entry.main)
        self.assertEqual(source.count("env_secrets.capture()"), 1)
        self.assertLess(source.index("parser.parse_args(argv)"), source.index("env_secrets.capture()"))
        self.assertLess(source.index("env_secrets.capture()"), source.index("asyncio.run"))


if __name__ == "__main__":
    unittest.main()
