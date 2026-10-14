"""Model provider layer (Assistant definition 12.12).

The pipeline talks to a provider, never to a vendor API, so the model behind it can change (a
local Ollama today, a hosted API later) without touching the pipeline. A provider offers:

* `classify_topics(transcript)` -> tuple of TopicGroup, or None when the answer is unusable;
* `analyze_case(messages, forced_type=None)` -> CaseAnalysis, or None.

Both return the same untrusted records as before; code validates and decides everything. Only
`ollama` exists. `claude` is a known future provider and is refused with a clear ConfigError until
it is implemented (when it is, text must be redacted of e-mail and phone numbers before it leaves
the machine, 12.12).
"""

from .classifier import Classifier
from .config import check_provider


class ModelProvider:
    """The minimal interface every provider implements."""

    name = ""

    def classify_topics(self, transcript, *, images=None):
        raise NotImplementedError

    def analyze_case(self, messages, forced_type=None, *, images=None):
        raise NotImplementedError


class OllamaProvider(Classifier, ModelProvider):
    """The first provider: the existing Ollama classifier, with reasoning (`think`) configurable."""

    name = "ollama"

    def classify_topics(self, transcript, *, images=None):
        """Split a closed window into topic groups. `transcript` is the numbered transcript, or the
        window's messages (the transcript is then built here)."""
        return self.split_topics(transcript, images=images)


def build_provider(config, **options):
    """The provider named by `config.model.provider`; ConfigError when it is not supported."""
    check_provider(config.model.provider)
    return OllamaProvider(config.ollama, think=config.model.think, **options)
