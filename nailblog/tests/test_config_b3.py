"""Step B3 configuration: the gemini text provider, section_thinking and the [links] table."""
import tempfile
import tomllib
import unittest
from pathlib import Path

from nailblog._errors import ConfigError
from nailblog.config import LinksConfig, TextModelConfig, load_config
from tests.helpers import load

ROOT = Path(__file__).resolve().parents[2]
HOME = "https://nailhub.ai/"
GEMINI = 'provider = "gemini"\nmodel = "gemini-test"\nkey_file = "/etc/nailblog/private/image-key.json"'


class GeminiProviderTests(unittest.TestCase):
    def invalid(self, pattern, **sections):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ConfigError, pattern):
                load(directory, None, **sections)

    def test_gemini_is_accepted_with_a_model_and_a_key_file(self):
        with tempfile.TemporaryDirectory() as directory:
            config = load(directory, None, text_model=GEMINI)
        self.assertEqual(config.text_model, TextModelConfig(provider="gemini", model="gemini-test",
                                                            key_file="/etc/nailblog/private/image-key.json"))
        self.assertEqual(config.text_model.section_thinking, "low")

    def test_gemini_needs_a_model_and_an_absolute_key_file(self):
        self.invalid("text_model.model is required for gemini", text_model='provider = "gemini"\nkey_file = "/k.json"')
        self.invalid("text_model.key_file is required for gemini", text_model='provider = "gemini"\nmodel = "m"')
        self.invalid("text_model.key_file must be an absolute path",
                     text_model='provider = "gemini"\nmodel = "m"\nkey_file = "key.json"')

    def test_other_providers_and_the_secret_rule_still_hold(self):
        self.invalid("ollama, deepseek or gemini", text_model='provider = "claude"')
        self.invalid("text_model.model is required for deepseek", text_model='provider = "deepseek"\nkey_file = "/k.json"')
        self.invalid("secrets must live in a key file", text_model=GEMINI + '\napi_key = "placeholder-not-a-key"')
        self.invalid("text_model.think must be false", text_model=GEMINI + "\nthink = true")
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(load(directory).text_model.provider, "ollama")           # the default is still local (O-2)

    def test_section_thinking_accepts_the_levels_and_nothing_else(self):
        for level in ("", "minimal", "low", "medium", "high"):
            with self.subTest(level=level), tempfile.TemporaryDirectory() as directory:
                config = load(directory, None, text_model=GEMINI + f'\nsection_thinking = "{level}"')
                self.assertEqual(config.text_model.section_thinking, level)
        for bad in ('"extreme"', "5", "true", '"LOW"'):
            with self.subTest(bad=bad):
                self.invalid("section_thinking", text_model=GEMINI + f"\nsection_thinking = {bad}")


class LinksTests(unittest.TestCase):
    def invalid(self, pattern, body):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ConfigError, pattern):
                load(directory, None, links=body)

    def test_defaults_point_at_the_home_page_until_phase_0_fixes_the_real_urls(self):
        with tempfile.TemporaryDirectory() as directory:
            config = load(directory)
        self.assertEqual(config.links, LinksConfig(HOME, HOME, HOME))
        self.assertEqual((config.links.jobs, config.links.salon_sale, config.links.app), (HOME, HOME, HOME))

    def test_values_are_read(self):
        body = 'jobs = "https://nailhub.ai/jobs"\nsalon_sale = "https://nailhub.ai/sang-tiem"\napp = "https://nailhub.ai/app?ref=blog"'
        with tempfile.TemporaryDirectory() as directory:
            config = load(directory, None, links=body)
        self.assertEqual(config.links, LinksConfig("https://nailhub.ai/jobs", "https://nailhub.ai/sang-tiem",
                                                   "https://nailhub.ai/app?ref=blog"))

    def test_a_target_must_be_https_on_the_site_host(self):
        for url in ("http://nailhub.ai/jobs", "https://evil.example/jobs", "https://nailhub.ai.evil.example/",
                    "https://user@nailhub.ai/", "https://nailhub.ai:8443/", "https://nailhub.ai/#x", "javascript:alert(1)",
                    "//nailhub.ai/jobs", "https://nailhub.ai/a b", "https://nailhub.ai/" + "x" * 300):
            with self.subTest(url=url):
                self.invalid("links.jobs must be an https URL on nailhub.ai", f'jobs = "{url}"')
        self.invalid("links.app must be a non-empty string", "app = 5")
        self.invalid("links.salon_sale must be a non-empty string", 'salon_sale = ""')

    def test_unknown_targets_and_secret_like_names_are_refused(self):
        self.invalid("links.blog: unknown configuration key", 'blog = "https://nailhub.ai/"')
        self.invalid("secrets must live in a key file", 'api_key = "https://nailhub.ai/"')

    def test_the_example_template_carries_the_links_table_equal_to_the_defaults(self):
        text = (ROOT / "config" / "nailblog.example.toml").read_text(encoding="utf-8")
        data = tomllib.loads(text)
        self.assertEqual(data["links"], {"jobs": HOME, "salon_sale": HOME, "app": HOME})
        self.assertEqual(data["text_model"]["provider"], "ollama")
        self.assertEqual(data["text_model"]["section_thinking"], "low")
        self.assertIn("TODO Phase 0", text)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "example.toml"
            path.write_text(text.replace("owner_ids = []", "owner_ids = [1603321775]").replace(
                "/var/lib/agentfleet/nailblog/", directory.replace("\\", "/") + "/"), encoding="utf-8")
            config = load_config(path)
        self.assertEqual((config.links, config.text_model.provider), (LinksConfig(HOME, HOME, HOME), "ollama"))


if __name__ == "__main__":
    unittest.main()
