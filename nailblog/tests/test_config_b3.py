"""Step B3 configuration: the Agy-only text provider (old text keys are removed) and the [links] table."""
import tempfile
import tomllib
import unittest
from pathlib import Path

from nailblog._errors import ConfigError
from nailblog.config import LinksConfig, TextModelConfig, load_config
from tests.helpers import load

ROOT = Path(__file__).resolve().parents[2]
HOME = "https://nailhub.ai/"
AGY = 'provider = "agy"\nmodel = "agy-test"'


class AgyProviderTests(unittest.TestCase):
    def invalid(self, pattern, **sections):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ConfigError, pattern):
                load(directory, None, **sections)

    def test_agy_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            config = load(directory, None, text_model=AGY)
        self.assertEqual(config.text_model, TextModelConfig(provider="agy", model="agy-test"))

    def test_other_providers_are_refused_and_the_secret_rule_still_holds(self):
        for provider in ("ollama", "deepseek", "gemini", "claude"):
            with self.subTest(provider=provider):
                self.invalid("text_model.provider must be agy", text_model=f'provider = "{provider}"')
        self.invalid("secrets must live in a key file", text_model=AGY + '\napi_key = "placeholder-not-a-key"')
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(load(directory).text_model.provider, "agy")           # the default is the Agy worker

    def test_old_text_keys_are_removed_not_ignored(self):
        for name, value in (("url", '"http://127.0.0.1:11434"'), ("key_file", '"/k.json"'), ("num_predict", "3000"),
                            ("think", "false"), ("section_thinking", '"low"')):
            with self.subTest(key=name):
                self.invalid(f"^removed_key:text_model[.]{name}$", text_model=AGY + f"\n{name} = {value}")


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
        self.assertEqual(data["text_model"]["provider"], "agy")
        self.assertNotIn("section_thinking", data["text_model"])
        self.assertIn("TODO Phase 0", text)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "example.toml"
            path.write_text(text.replace("owner_ids = []", "owner_ids = [1603321775]").replace(
                "/var/lib/agentfleet/nailblog/", directory.replace("\\", "/") + "/"), encoding="utf-8")
            config = load_config(path)
        self.assertEqual((config.links, config.text_model.provider), (LinksConfig(HOME, HOME, HOME), "agy"))


if __name__ == "__main__":
    unittest.main()
