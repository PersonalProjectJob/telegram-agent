"""Model provider layer (Assistant definition 12.12).

The pipeline talks to a provider, never to a vendor API, so the model behind it can change (a
local Ollama today, a hosted API later) without touching the pipeline. A provider offers:

* `classify_topics(transcript)` -> tuple of TopicGroup, or None when the answer is unusable;
* `analyze_case(messages, forced_type=None)` -> CaseAnalysis, or None.

Both return untrusted records validated by existing parsers. Ollama supports local image input;
cloud providers require explicit consent, redact contact text and fail closed on images.
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
    if config.model.provider == "ollama":
        provider = OllamaProvider(config.ollama, think=config.model.think, **options)
        provider.max_output = config.model.max_output
        return provider
    return CloudProvider(config, **options)


class CloudProvider(OllamaProvider):
    """Reuse existing untrusted parsers; cloud data requires explicit consent."""
    supports_vision = False
    rejects_images = True
    def __init__(self, config, **options):
        super().__init__(config.ollama, think=False, **options)
        self.model_config = config.model
        self.name = config.model.provider
    def _messages(self, system, messages, images):
        if images:
            return [{'role':'user','content':'','images':True}]
        return super()._messages(system, messages, images)
    def _ask(self, body, parse, attempts=2):
        from .ai_transport import complete, read_key, redact, ProviderError
        from .model_lock import MODEL_LOCK, LockUnavailable
        c = self.model_config
        if not c.cloud_consent or any('images' in m for m in body['messages']):
            return None
        messages = [dict(m, content=redact(m['content'])) for m in body['messages']]
        try:
            key = read_key(c.key_file, c.provider+'|'+c.base_url, group_name='tglistener')
            for _ in range(attempts):
                with MODEL_LOCK.enter():
                    value = complete(c.provider, c.base_url, c.name, messages, body['format'], key=key,
                                     timeout=c.timeout_seconds, max_output=c.max_output)
                result = parse(__import__('json').dumps(value))
                if result is not None:
                    return result
        except (ProviderError, LockUnavailable):
            return None
        return None
