"""Autoflow shared contracts: new config keys loaded through the real TOML loader."""
import tempfile
import unittest
from pathlib import Path

from nailblog._errors import ConfigError
from nailblog.config import (AGY_SLOT_CATEGORIES, CATEGORIES, DEFAULT_SLOT_CATEGORIES, AdminConfig, AutoflowConfig, BudgetConfig,
                             DailyConfig, ImageConfig, _is_secret_name, load_config)
from tests.helpers import load

ROOT = Path(__file__).resolve().parents[2]
GUID = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"
LIVE = {"mode": "live"}
CONTRACT = 'verified = true\nverification_ref = "report-1"'
IMAGE_AGY = 'provider = "subscription"\ncandidates = 1\nmax_rounds = 1\nreview_bypass = true'
SUBSCRIPTION = 'queue_dir = "/var/lib/nailblog-subscription/queue"\nprivate_dir = "/home/w/private"'
IDS = "\n".join(f'{slug} = "{GUID}"' for slug in CATEGORIES)


def live_sections(**extra):
    """A complete live Agy autoflow configuration; keyword args replace or add raw TOML tables (None removes)."""
    sections = {"contract": CONTRACT + '\nenvironment = "staging"', "image": IMAGE_AGY, "subscription": SUBSCRIPTION,
                "budget": 'ledger_db_path = "/var/lib/agentfleet/nailblog-ledger/ledger.sqlite3"',
                "admin": "daily_publish_limit = 2", "admin__category_ids": IDS,
                "autoflow": 'enabled = true\nruns_dir = "/var/lib/agentfleet/nailblog/runs"'}
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
        self.assertEqual(config.daily.slot_categories, AGY_SLOT_CATEGORIES)
        self.assertEqual(config.slots, tuple(AGY_SLOT_CATEGORIES))

    def test_live_agy_without_autoflow_loads(self):
        config = self.load(LIVE, **live_sections(autoflow=None, admin__category_ids=None))
        self.assertEqual((config.image.provider, config.text_model.provider), ("subscription", "agy"))
        self.assertFalse(config.autoflow.enabled)

    def test_old_image_keys_are_removed_not_ignored(self):
        self.invalid("^removed_key:image.key_source$", image='key_source = "env"')
        self.invalid("^removed_key:image.key_file$", image='key_file = "/etc/nailblog/k.json"')
        self.invalid("^removed_key:image.model_cover$", image='model_cover = "m"')
        self.invalid("^removed_key:image.cover_size$", image='cover_size = "1K"')

    def test_old_budget_keys_and_cloudflare_are_removed_not_ignored(self):
        self.invalid("^removed_key:budget.daily_usd$", budget="daily_usd = 3.0")
        self.invalid("^removed_key:budget.image_unit_prices$", budget__image_unit_prices='m = 0.1')
        self.invalid("^removed_key:budget.text_prices$", **{'budget__text_prices__m': "input_per_m = 1\noutput_per_m = 1"})
        self.invalid("^removed_key:cloudflare$", cloudflare='account_id = ""')
        self.assertEqual(self.load(budget='ledger_db_path = "/var/lib/agentfleet/l.sqlite3"').budget.ledger_db_path,
                         "/var/lib/agentfleet/l.sqlite3")


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

    def test_three_slots_allow_three(self):
        slots = {"slots": ["06:00", "12:00", "19:00"]}
        groups = '"06:00" = ["hot-trend"]\n"12:00" = ["nail-job"]\n"19:00" = ["nail-salon", "did-you-know"]'
        self.assertEqual(self.load(slots, admin="daily_publish_limit = 3", daily__slot_categories=groups)
                         .admin.daily_publish_limit, 3)

    def test_four_slots_allow_four_but_never_five(self):
        four = {"slots": list(AGY_SLOT_CATEGORIES)}
        self.assertEqual(self.load(four, admin="daily_publish_limit = 4").admin.daily_publish_limit, 4)
        self.invalid("1..4", four, admin="daily_publish_limit = 5")
        five = {"slots": ["06:00", "10:00", "12:00", "15:00", "19:00"]}
        groups = "\n".join(f'"{slot}" = ["hot-trend"]' for slot in five["slots"])
        self.invalid("1..4", five, admin="daily_publish_limit = 5", daily__slot_categories=groups)
        self.assertEqual(self.load(five, admin="daily_publish_limit = 4", daily__slot_categories=groups)
                         .admin.daily_publish_limit, 4)


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

    def test_four_slots_without_the_table_get_the_new_groups(self):
        config = self.load({"slots": ["19:00", "15:00", "10:00", "06:00"]})
        self.assertEqual(config.daily.slot_categories, {
            "06:00": ("hot-trend", "nail-job"), "10:00": ("did-you-know", "hot-trend"),
            "15:00": ("nail-salon", "nail-job"), "19:00": ("nail-salon", "did-you-know")})

    def test_two_old_slots_without_the_table_keep_the_old_groups(self):
        self.assertEqual(self.load({"slots": ["06:00", "19:00"]}).daily.slot_categories, DEFAULT_SLOT_CATEGORIES)

    def test_custom_slots_without_the_table_keep_loading(self):
        self.assertEqual(self.load({"slots": ["07:00"]}).daily.slot_categories, {})


class AutoflowTests(ConfigCase):
    def test_a_complete_enabled_configuration_loads(self):
        config = self.load(LIVE, **live_sections())
        self.assertEqual(config.autoflow, AutoflowConfig(True, 0.0, "/var/lib/agentfleet/nailblog/runs"))

    def test_each_requirement_fails_closed(self):
        self.invalid("autoflow.enabled requires service.mode = live", None, **live_sections())
        self.invalid("autoflow.enabled requires admin.category_ids for all of", LIVE,
                     **live_sections(admin__category_ids=IDS.splitlines()[0]))
        self.invalid("autoflow.enabled requires admin.category_ids for all of", LIVE, **live_sections(admin__category_ids=None))
        self.invalid("autoflow.enabled requires autoflow.runs_dir", LIVE, **live_sections(autoflow="enabled = true"))
        self.invalid("autoflow.enabled requires budget.ledger_db_path", LIVE, **live_sections(budget=None))
        self.invalid("budget.ledger_db_path must be an absolute path", LIVE,
                     **live_sections(budget='ledger_db_path = "ledger.sqlite3"'))
        self.invalid("subscription requires staging", LIVE, **live_sections(contract=CONTRACT))
        self.invalid("autoflow.enabled requires admin.daily_publish_limit >= the number of service.slots", LIVE,
                     **live_sections(admin="daily_publish_limit = 1"))

    def test_run_usd_is_a_removed_key(self):
        self.invalid("^removed_key:autoflow.run_usd$", LIVE, **live_sections(autoflow='enabled = true\nrun_usd = 1.0'))
        self.invalid("^removed_key:autoflow.run_usd$", autoflow="run_usd = 4.0")

    def test_four_slot_autoflow_needs_a_limit_of_four(self):
        four = dict(LIVE, slots=list(AGY_SLOT_CATEGORIES))
        self.invalid("admin.daily_publish_limit >= the number of service.slots", four, **live_sections())
        config = self.load(four, **live_sections(admin="daily_publish_limit = 4"))
        self.assertTrue(config.autoflow.enabled)

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
        for name in ("text_agent", "text_timeout_seconds", "create_timeout_seconds", "category_ids",
                     "publish_timeout_seconds", "slot_categories", "enabled", "runs_dir"):
            self.assertFalse(_is_secret_name(name), name)

    def test_errors_never_carry_a_key_value(self):
        marker = "AIzaSy-test-marker-value"
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ConfigError) as caught:
                load(directory, LIVE, **live_sections(image=IMAGE_AGY + f'\nkey_value = "{marker}"'))
        self.assertNotIn(marker, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
