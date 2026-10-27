"""Text model: schema-validated JSON through the Agy worker (agy_text.AgyText), the only text route.

There is no paid or local-API route any more. `TextModel` is a thin async adapter for the legacy
`await text.ask(purpose, system, user, schema, ...)` callers (planner, writer.write_article, CLI): it runs the
synchronous `AgyText.ask` in a thread and never holds MODEL_LOCK (the worker takes the shared lock itself).
Prompts, source text and model output are never logged.
"""

import asyncio
import json
import logging
import time

LOG = logging.getLogger(__name__)


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


def _ask_over_transport(transport):
    """TEST SEAM, never for production: adapt an injected `transport(url, payload, headers, timeout, provider)` whose
    envelope has the shape `{"message": {"content": <json text>}}` to the Agy `ask` contract. No network."""
    def ask(prompt, schema, *, purpose, deadline=None):
        payload = {"format": schema, "messages": [{"role": "user", "content": prompt}], "purpose": purpose}
        return json.loads(transport("agy://test-seam", payload, {}, 0, "agy")["message"]["content"])
    return ask


class TextModel:
    """Async adapter over `ask(prompt, schema, *, purpose, deadline=None)` (default `AgyText(config).ask`).

    `_test_transport` exists only so tests can script the model; production code (`__main__`) never passes it, and an
    unknown keyword raises TypeError."""

    def __init__(self, config, *, ask=None, stop=None, before_dispatch=None, _test_transport=None):
        """`_test_transport`: TEST ONLY, must stay None in production."""
        self.config = config.text_model
        self.stop, self.before_dispatch = stop, before_dispatch
        if ask is None and _test_transport is not None:
            ask = _ask_over_transport(_test_transport)
        if ask is None:
            from .agy_text import AgyText
            ask = AgyText(config).ask
        self._ask = ask

    async def ask(self, purpose, system, user, schema, *, max_tokens=None, context=None, light=False, attempts=2):
        """One JSON object matching `schema`. The worker validates the schema; this re-checks it locally.
        Errors surface as TextModelError (the AgyTextError code is the message)."""
        if self.before_dispatch is not None:
            self.before_dispatch()
        started = time.monotonic()
        try:
            prompt = system + chr(10) * 2 + user
            result = await asyncio.to_thread(self._ask, prompt, schema, purpose=purpose)
        except Exception as error:
            code = getattr(error, "code", None)
            LOG.info("text purpose=%s ms=%d outcome=%s", purpose, _ms(started), code or type(error).__name__)
            raise TextModelError(f"model call failed: {code or type(error).__name__}") from None
        problems = check_schema(schema, result)
        LOG.info("text purpose=%s ms=%d outcome=%s", purpose, _ms(started), "valid" if not problems else "invalid_schema")
        if problems:
            raise TextModelError("model JSON does not match the schema: " + "; ".join(problems[:3]))
        return result


def _ms(started):
    return int((time.monotonic() - started) * 1000)
