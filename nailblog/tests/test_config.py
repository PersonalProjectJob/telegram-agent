import tempfile
import unittest
from pathlib import Path

from nailblog._errors import ConfigError
from nailblog.config import NailBlogConfig, load_config
from tests.helpers import OWNER, load, write_config

ROOT = Path(__file__).resolve().parents[2]
LIVE_CONTRACT = 'verified = true\nverification_ref = "probe-2026-10-20"'
LIVE_IMAGE = 'model_body = "b"\nmodel_cover = "c"\nreview_model = "r"\nkey_file = "/etc/nailblog/private/image-key.json"'


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
        self.assertEqual((config.schedule.window_start_hours, config.schedule.window_end_hours,
                          config.schedule.approve_deadline_hours), (24, 3, 2))
        self.assertEqual((config.text_model.provider, config.text_model.model, config.text_model.think),
                         ("ollama", "qwen3.5:9b", False))
        self.assertEqual((config.image.candidates, config.image.max_rounds, config.budget.daily_usd), (2, 2, 3.0))
        self.assertEqual((config.contract.api_origin, config.contract.site_origin, config.contract.verified),
                         ("https://api.nailhub.ai", "https://nailhub.ai", False))

    def test_full_config_parses_prices_and_deepseek(self):
        with tempfile.TemporaryDirectory() as directory:
            config = load(directory, None,
                          text_model='provider = "deepseek"\nmodel = "deepseek-chat"\nkey_file = "/etc/nailblog/private/model-key.json"',
                          image='provider = "openai"\n' + LIVE_IMAGE + "\ncandidates = 3\nmax_rounds = 1",
                          budget='daily_usd = 1.5\n[budget.image_prices]\n"c" = 0.04',
                          **{'budget.text_prices."deepseek-chat"': "input_per_m = 0.27\noutput_per_m = 1.1"},
                          console='revision = "abc"')
        self.assertEqual((config.text_model.provider, config.image.provider, config.image.candidates), ("deepseek", "openai", 3))
        self.assertEqual(config.budget.text_prices, {"deepseek-chat": (0.27, 1.1)})
        self.assertEqual((config.budget.image_prices, config.budget.daily_usd, config.console_revision), ({"c": 0.04}, 1.5, "abc"))

    def test_live_requires_verified_contract_and_reference(self):
        self.invalid("live mode needs", {"mode": "live"}, image=LIVE_IMAGE)
        self.invalid("live mode needs", {"mode": "live"}, image=LIVE_IMAGE, contract="verified = true")
        self.invalid("live mode needs", {"mode": "live"}, image=LIVE_IMAGE,
                     contract='verified = true\nverification_ref = "  "')
        self.invalid("live mode needs", {"mode": "live"}, image=LIVE_IMAGE,
                     contract='verified = false\nverification_ref = "x"')
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(load(directory, {"mode": "live"}, image=LIVE_IMAGE, contract=LIVE_CONTRACT).mode, "live")

    def test_live_requires_image_models_and_key_file(self):
        self.invalid("requires image models", {"mode": "live"}, contract=LIVE_CONTRACT)

    def test_mode_must_be_preview_or_live(self):
        self.invalid("service.mode", {"mode": "dry"})

    def test_paths_must_be_absolute(self):
        self.invalid("service.db_path must be an absolute", {"db_path": "relative/db.sqlite3"})
        self.invalid("service.db_path must be a non-empty", {"db_path": None})
        self.invalid("telegram.session must be an absolute", telegram='session = "nailblog.session"')
        self.invalid("text_model.key_file must be an absolute", text_model='key_file = "model-key.json"')
        self.invalid("image.key_file must be an absolute", image='key_file = "image-key.json"')
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

    def test_text_model_is_local_first_and_no_thinking(self):
        self.invalid("text_model.provider", text_model='provider = "openai"')
        self.invalid("text_model.think", text_model="think = true")
        self.invalid("text_model.think", text_model='think = "false"')
        for url in ("https://127.0.0.1:11434", "http://example.com:11434", "http://user@127.0.0.1:11434",
                    "http://127.0.0.1:11434/api"):
            with self.subTest(url=url):
                self.invalid("localhost Ollama", text_model=f'url = "{url}"')
        self.invalid("text_model.model", text_model='model = ""')
        self.invalid("model is required for deepseek", text_model='provider = "deepseek"\nkey_file = "/k.json"')
        self.invalid("key_file is required for deepseek", text_model='provider = "deepseek"\nmodel = "m"')

    def test_image_validation(self):
        self.invalid("image.provider", image='provider = "stability"')
        self.invalid("image.candidates", image="candidates = 0")
        self.invalid("image.candidates", image="candidates = 9")
        self.invalid("image.max_rounds", image="max_rounds = 0")
        self.invalid("image.model_body", image="model_body = 7")

    def test_budget_validation(self):
        self.invalid("budget.daily_usd", budget="daily_usd = -1")
        self.invalid("budget.daily_usd", budget='daily_usd = "3"')
        self.invalid("budget.daily_usd", budget="daily_usd = nan")
        self.invalid("needs input_per_m and output_per_m", **{'budget.text_prices."m"': "input_per_m = 1"})
        self.invalid("input_per_m", **{'budget.text_prices."m"': 'input_per_m = -1\noutput_per_m = 1'})
        self.invalid("budget.image_prices", budget="[budget.image_prices]\nm = true")

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
        self.assertEqual((config.mode, config.owner_ids, config.slots), ("preview", (OWNER,), ("06:00", "19:00")))


if __name__ == "__main__":
    unittest.main()
