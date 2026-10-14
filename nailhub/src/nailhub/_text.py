"""Discard model thinking without importing another service."""
import re

_THINK_BLOCK = re.compile(r"^\s*<think>.*?</think>\s*", re.DOTALL)

def strip_thinking(content):
    """Drop a leading <think>...</think> block that some servers leave inside `content`.

    Ollama normally returns the reasoning in `message.thinking`, which this module never reads.
    """
    return _THINK_BLOCK.sub("", content, count=1) if isinstance(content, str) else content

