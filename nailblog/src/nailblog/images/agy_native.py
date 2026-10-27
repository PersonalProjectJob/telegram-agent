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
