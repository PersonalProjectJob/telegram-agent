"""Text model: schema-validated JSON from a local Ollama (default, O-2), DeepSeek or Gemini.

Every call goes through an injected `call` (Service.model_call): that enters MODEL_LOCK as a background
job, checks the daily budget for paid providers and records usage. Prompts, source text and model output
are never logged.
"""

import json
import logging
import time

from . import gemini_text
from .ai_transport import ENDPOINTS, TEXT_INPUT_LIMIT, ProviderError, complete, read_key, request_json

LOG = logging.getLogger(__name__)
DEEPSEEK_BASE = ENDPOINTS["deepseek"]
KEY_GROUP = "tgnailblog"


class TextModelError(RuntimeError):
    """The model call failed or its JSON never matched the schema. Messages carry codes, never content."""


def object_schema(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def array_schema(items, minimum=0, maximum=None):
    schema = {"type": "array", "items": items, "minItems": minimum}
    if maximum is not None:
        schema["maxItems"] = maximum
    return schema


def string_schema(minimum=0, maximum=None):
    schema = {"type": "string", "minLength": minimum}
    if maximum is not None:
        schema["maxLength"] = maximum
    return schema


_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def _is_type(value, name):
    if name == "integer":
        return type(value) is int
    if name == "number":
        return type(value) in (int, float)
    return isinstance(value, _TYPES[name])


def check_schema(schema, value, path="$"):
    """Validate the JSON-Schema subset used here; return short problem strings (empty list = valid)."""
    wanted = schema.get("type")
    if wanted is not None:
        names = wanted if isinstance(wanted, list) else [wanted]
        if not any(_is_type(value, name) for name in names):
            return [f"{path}: expected {'/'.join(names)}"]
    if "enum" in schema and value not in schema["enum"]:
        return [f"{path}: value not allowed"]
    problems = []
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            problems.append(f"{path}: too short")
        if len(value) > schema.get("maxLength", len(value)):
            problems.append(f"{path}: too long (max {schema['maxLength']})")
    elif type(value) in (int, float):
        if value < schema.get("minimum", value):
            problems.append(f"{path}: below {schema['minimum']}")
        if value > schema.get("maximum", value):
            problems.append(f"{path}: above {schema['maximum']}")
    elif isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            problems.append(f"{path}: needs at least {schema['minItems']} items")
        if len(value) > schema.get("maxItems", len(value)):
            problems.append(f"{path}: needs at most {schema['maxItems']} items")
        if "items" in schema:
            for index, item in enumerate(value):
                problems += check_schema(schema["items"], item, f"{path}[{index}]")
    elif isinstance(value, dict):
        properties = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                problems.append(f"{path}.{name}: missing")
        for name, item in value.items():
            if name in properties:
                problems += check_schema(properties[name], item, f"{path}.{name}")
            elif schema.get("additionalProperties") is False:
                problems.append(f"{path}.{name}: unexpected field")
    return problems


class TextModel:
    """`call` is an async callable with Service.model_call's signature."""

    def __init__(self, config, call, *, transport=request_json, key_reader=read_key, stop=None, before_dispatch=None):
        self.config = config.text_model
        self.call = call
        self.transport = transport
        self.key_reader = key_reader
        self.stop = stop
        self.before_dispatch = before_dispatch

    def _complete(self, messages, schema, max_tokens, context, light=False, usage_sink=None):
        cfg = self.config
        output = min(cfg.num_predict, max_tokens)
        raw_transport = gemini_text.request_gemini if cfg.provider == 'gemini' and self.transport is request_json else self.transport
        dispatched = 0
        def bounded_transport(url, payload, headers, timeout, provider):
            nonlocal dispatched
            if len(json.dumps(payload).encode()) > TEXT_INPUT_LIMIT:
                raise ProviderError('input_too_large')
            if self.before_dispatch is not None:
                try:
                    self.before_dispatch()
                except Exception:
                    from .images.pipeline import DispatchPaused
                    raise DispatchPaused('workflow_paused_before_dispatch', dispatched=dispatched>0) from None
            dispatched += 1
            envelope = raw_transport(url,payload,headers,timeout,provider)
            if provider == 'deepseek' and usage_sink is not None:
                reported = envelope.get('usage',{})
                if (not isinstance(reported,dict) or any(type(reported.get(name)) is not int or reported[name] < 0
                        for name in ('prompt_tokens','completion_tokens'))):
                    usage_sink({'_uncertain':True})
            return envelope
        if cfg.provider == "gemini":
            key = self.key_reader(cfg.key_file, gemini_text.KEY_SCOPE, group_name=KEY_GROUP)
            return gemini_text.complete(cfg.model, messages, schema, key=key, timeout=cfg.timeout_seconds,
                                        max_output=output, thinking=(cfg.section_thinking or None) if light else None,
                                        transport=bounded_transport, usage_sink=usage_sink)
        if cfg.provider == "ollama":
            return complete("ollama", cfg.url, cfg.model, messages, schema, timeout=cfg.timeout_seconds,
                            max_output=output, think=False, context=context, transport=bounded_transport,
                            usage_sink=usage_sink)
        key = self.key_reader(cfg.key_file, "deepseek|" + DEEPSEEK_BASE, group_name=KEY_GROUP)
        return complete("deepseek", DEEPSEEK_BASE, cfg.model, messages, schema, key=key, timeout=cfg.timeout_seconds,
                        max_output=output, transport=bounded_transport, usage_sink=usage_sink)

    async def ask(self, purpose, system, user, schema, *, max_tokens, context=8192, light=False, attempts=2):
        """One JSON object matching `schema`; by default one repair call that carries only a short error note.

        `attempts` (default 2) is the total number of tries; the outline call asks for 3 and each retry then carries
        the concrete validation errors (field path + message).

        `light` marks a section/repair call: Gemini then runs at [text_model] section_thinking (low by default)."""
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        for attempt in range(1, attempts + 1):
            started = time.monotonic()
            try:
                result = await self.call(self._complete, messages, schema, max_tokens, context, light,
                                         provider=self.config.provider, model=self.config.model, stop=self.stop)
            except ProviderError as error:
                outcome = error.code
                if error.code != "invalid_json" or attempt == attempts:
                    LOG.info("text purpose=%s attempt=%d ms=%d outcome=%s", purpose, attempt, _ms(started), outcome)
                    raise TextModelError(f"model call failed: {error.code}") from None
                note = "đầu ra không phải JSON đọc được"
            else:
                problems = check_schema(schema, result)
                outcome = "valid" if not problems else "invalid_schema"
                if not problems:
                    LOG.info("text purpose=%s attempt=%d ms=%d outcome=valid", purpose, attempt, _ms(started))
                    return result
                if attempt == attempts:
                    LOG.info("text purpose=%s attempt=%d ms=%d outcome=%s", purpose, attempt, _ms(started), outcome)
                    raise TextModelError("model JSON does not match the schema: " + "; ".join(problems[:3]))
                note = "; ".join(problems[:(3 if attempts <= 2 else 6)])
            LOG.info("text purpose=%s attempt=%d ms=%d outcome=%s", purpose, attempt, _ms(started), outcome)
            messages = messages + [{"role": "user", "content": (
                f"Kết quả trước không hợp lệ ({note}). Trả lại duy nhất một JSON đúng schema, sửa các lỗi trên.")}]


def _ms(started):
    return int((time.monotonic() - started) * 1000)
