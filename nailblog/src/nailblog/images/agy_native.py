"""Pinned agy 1.2.11 native tool-result decoder. Unknown wire shapes fail closed.

Only the typed generate_image result is authoritative; prose/CLI stdout paths are not receipts.
"""
import json
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import ImageError


def fields(raw):
    if not isinstance(raw, bytes) or len(raw) > 2 * 1024 * 1024:
        raise ImageError('cli_native_bounds')
    pos, out = 0, {}

    def integer():
        nonlocal pos
        value = 0
        for shift in range(0, 70, 7):
            if pos >= len(raw):
                raise ImageError('cli_native_wire')
            byte = raw[pos]; pos += 1
            value |= (byte & 127) << shift
            if byte < 128:
                return value
        raise ImageError('cli_native_wire')

    while pos < len(raw):
        tag = integer(); number, wire = tag >> 3, tag & 7
        if not number or len(out) > 256:
            raise ImageError('cli_native_wire')
        if wire == 0:
            value = integer()
        elif wire in (1, 2, 5):
            size = integer() if wire == 2 else (8 if wire == 1 else 4)
            if size > len(raw) - pos:
                raise ImageError('cli_native_wire')
            value = raw[pos:pos + size]; pos += size
        else:
            raise ImageError('cli_native_wire')
        out.setdefault(number, []).append(value)
    return out


def one(message, number):
    values = message.get(number, [])
    if len(values) != 1:
        raise ImageError('cli_native_shape')
    return values[0]


def native_call(raw, *, prompt, image_name):
    """Observed type15 invocation; exactly one native image tool, no other calls."""
    try:
        root = fields(raw)
        if set(root) != {1, 4, 5, 20} or one(root, 1) != 15 or one(root, 4) != 3:
            raise ImageError('cli_native_invocation_shape')
        call = fields(one(fields(one(root, 20)), 7))
        args = json.loads(one(call, 3))
        if (one(call, 2) != b'generate_image' or args.get('Prompt') != prompt or
                args.get('ImageName') != image_name or args.get('AspectRatio') != '16:9' or args.get('ImagePaths')):
            raise ImageError('cli_native_request_mismatch')
        return one(call, 1).decode()
    except ImageError:
        raise
    except (ValueError, TypeError, UnicodeError, KeyError):
        raise ImageError('cli_native_invocation_shape') from None


def final_text(raw):
    """Only the observed final assistant-text shape, never a tool-call receipt."""
    try:
        root=fields(raw)
        if set(root)!={1,4,5,20} or one(root,1)!=15 or one(root,4)!=3:
            raise ImageError('cli_native_final_text_shape')
        if 4 in fields(one(root,5)):
            raise ImageError('cli_native_final_text_shape')
        body=fields(one(root,20))
        if set(body)!={1,6,8,12,14} or one(body,12)!=2:
            raise ImageError('cli_native_final_text_shape')
        text=one(body,1).decode()
        if not 1<=len(text)<=12000 or one(body,8).decode()!=text or not one(body,6).decode():
            raise ImageError('cli_native_final_text_shape')
        signature=fields(one(body,14))
        if set(signature)!={2} or not isinstance(one(signature,2),bytes) or not one(signature,2):
            raise ImageError('cli_native_final_text_shape')
        return text
    except ImageError:
        raise
    except (TypeError,ValueError,UnicodeError):
        raise ImageError('cli_native_final_text_shape') from None


def native_image(raw, *, conversation, prompt, image_name, brain):
    """Extract only the observed type132/format0 successful native generation record."""
    try:
        root = fields(raw)
        if one(root, 1) != 132 or one(root, 4) != 3:
            raise ImageError('cli_native_status')
        # The observed successful payload has fields 1,4,5,140 only. Permissions
        # and error_details are nullable SQLite columns, not a fabricated field34.
        if set(root) != {1, 4, 5, 140}:
            raise ImageError('cli_native_shape')
        call = fields(one(fields(one(root, 5)), 4))
        args = json.loads(one(call, 3))
        if (one(call, 2) != b'generate_image' or args.get('Prompt') != prompt or
                args.get('ImageName') != image_name or args.get('AspectRatio') != '16:9' or
                args.get('ImagePaths')):
            raise ImageError('cli_native_request_mismatch')
        result = fields(one(fields(one(root, 140)), 2))
        wrapped = fields(one(result, 6))
        if one(wrapped, 1) != b'type.googleapis.com/gemini_coder.Step':
            raise ImageError('cli_native_shape')
        step = fields(one(wrapped, 2))
        if one(step, 1) != 91 or one(step, 4) != 3:
            raise ImageError('cli_native_status')
        generated = fields(one(step, 104))
        if (one(generated, 1).decode() != prompt or one(generated, 4).decode() != image_name or
                one(generated, 7) != b'16:9'):
            raise ImageError('cli_native_request_mismatch')
        backend = one(generated, 5).decode()
        if backend != 'gemini-3.1-flash-image':
            raise ImageError('cli_native_backend_changed')
        asset = fields(one(generated, 6))
        if one(asset, 1) not in (b'image/jpeg', b'image/png'):
            raise ImageError('cli_native_image_type')
        uri = urlparse(one(asset, 5).decode())
        source = Path(unquote(uri.path))
        expected = Path(brain) / conversation
        if (uri.scheme != 'file' or uri.netloc or source.parent != expected or
                source.suffix.lower() not in ('.jpg', '.png', '.jpeg') or '..' in source.parts):
            raise ImageError('cli_native_source_outside_conversation')
        return {'source': str(source), 'backend_model': backend, 'tool_call_id': one(call, 1).decode()}
    except ImageError:
        raise
    except (ValueError, TypeError, UnicodeError, KeyError):
        raise ImageError('cli_native_shape') from None


# ---- text jobs (nailblog-agy-text-v1). Pure functions, no I/O, run on any OS. ----
HELD = 'cli_text_held_'
ASSISTANT_BODY = ({1, 6, 8, 12, 14}, {1, 3, 6, 8, 11, 12, 14})


def _held(why):
    return ImageError(HELD + why)


def collapse_json(text, *, limit=8):
    """Collapse only byte-identical repeated JSON objects; never choose among alternatives.

    agy may print the same JSON several times. Identical copies are one answer;
    different copies, trailing garbage or non-objects are a HOLD.
    """
    if not isinstance(text, str):
        raise _held('bad_json')
    decoder, offset, raws, values = json.JSONDecoder(), 0, [], []
    while True:
        while offset < len(text) and text[offset].isspace():
            offset += 1
        if offset >= len(text):
            break
        try:
            value, end = decoder.raw_decode(text, offset)
        except ValueError:
            raise _held('bad_json') from None
        raws.append(text[offset:end]); values.append(value); offset = end
        if len(values) > limit:
            break
    if not 1 <= len(values) <= limit or any(raw != raws[0] for raw in raws):
        raise _held('nonidentical_json')
    if not isinstance(values[0], dict):
        raise _held('object_required')
    return values[0], len(values)


def user_prompt(raw, prompt):
    """Observed type14 user record; its prompt mirror must equal what was dispatched."""
    try:
        root = fields(raw)
        if set(root) != {1, 4, 5, 19} or one(root, 1) != 14 or one(root, 4) != 3:
            raise _held('native_user')
        body = fields(one(root, 19))
        if set(body) != {2, 3, 4, 12}:
            raise _held('native_user')
        mirror = fields(one(body, 3))
        wanted = prompt.encode()
        if one(body, 2) != wanted or set(mirror) != {1} or one(mirror, 1) != wanted:
            raise _held('native_user_prompt')
        return True
    except ImageError as error:
        raise error if str(error).startswith(HELD) else _held('native_wire') from None
    except (TypeError, ValueError, UnicodeError, AttributeError):
        raise _held('native_user') from None


def assistant_text(raw, *, limit=64000):
    """Observed type15 assistant answer. Any tool-call metadata or unknown field is a HOLD."""
    try:
        root = fields(raw)
        if set(root) != {1, 4, 5, 20} or one(root, 1) != 15 or one(root, 4) != 3:
            raise _held('native_assistant')
        if 4 in fields(one(root, 5)):
            raise _held('tool_call_forbidden')
        body = fields(one(root, 20))
        if set(body) not in ASSISTANT_BODY or one(body, 12) != 2:
            raise _held('native_assistant')
        text, mirror, marker = one(body, 1), one(body, 8), one(body, 6)
        if (not isinstance(text, bytes) or not 1 <= len(text) <= limit or text != mirror or
                not isinstance(marker, bytes) or not marker):
            raise _held('native_assistant')
        if 3 in body and not (isinstance(one(body, 3), bytes) and isinstance(one(body, 11), bytes)):
            raise _held('native_assistant')
        signature = fields(one(body, 14))
        if set(signature) != {2} or not isinstance(one(signature, 2), bytes) or not one(signature, 2):
            raise _held('native_assistant')
        return text.decode()
    except ImageError as error:
        raise error if str(error).startswith(HELD) else _held('native_wire') from None
    except (TypeError, ValueError, UnicodeError, AttributeError):
        raise _held('native_assistant') from None


def native_text(rows, prompt):
    """Validate a text-only conversation: user prompt first, then assistant turns, nothing else.

    Returns the last assistant text plus the raw records. An unknown step type,
    status, shape or a trailing non-assistant row raises a HOLD error.
    """
    rows = list(rows)
    if (not 2 <= len(rows) <= 16 or [row[0] for row in rows] != list(range(len(rows))) or
            any(row[1] not in (14, 15) or (row[2], row[3]) != (3, 0) or row[5] is not None or row[6] is not None
                for row in rows) or rows[0][1] != 14 or rows[-1][1] != 15):
        raise _held('native_rows')
    user_prompt(bytes(rows[0][4]), prompt)
    final_raw, final = None, None
    for row in rows[1:]:
        if row[1] == 14:
            raise _held('native_rows')
        final_raw = bytes(row[4]); final = assistant_text(final_raw)
    return {'final_text': final, 'final_raw': final_raw, 'user_raw': bytes(rows[0][4]),
            'steps': [row[1] for row in rows]}


def salvage_text(rows, prompt):
    """After a CLI timeout: the finished final assistant answer, or None. Never raises."""
    try:
        return native_text(rows, prompt)
    except (ImageError, TypeError, IndexError, ValueError, UnicodeError):
        return None
