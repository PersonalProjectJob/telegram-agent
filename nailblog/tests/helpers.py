"""Test fixtures: real config/state objects in temporary directories, no network."""
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from nailblog.config import NailBlogConfig, load_config

OWNER = 1603321775

def write_config(directory, service=None, **sections):
    """Write a valid config; `service` overrides/omits (None) [service] keys, `sections` add raw TOML tables."""
    values = {"mode": "preview", "db_path": (Path(directory) / "nailblog.sqlite3").as_posix(),
              "owner_ids": [OWNER]}
    for name, value in (service or {}).items():
        if value is None:
            values.pop(name, None)
        else:
            values[name] = value
    lines = ["[service]"] + [f"{name} = {json.dumps(value)}" for name, value in values.items()]
    for name, body in sections.items():
        lines += [f"[{name.replace('__', '.')}]", body]
    path = Path(directory) / "nailblog.toml"
    path.write_text(chr(10).join(lines) + chr(10), encoding="utf-8")
    return path


def load(directory, service=None, **sections):
    return load_config(write_config(directory, service, **sections))


def settings(directory, **values):
    return replace(NailBlogConfig(str(Path(directory) / "nailblog.sqlite3"), owner_ids=(OWNER,)), **values)


def dm(**values):
    """Event shaped like a Telethon NewMessage event for an Owner DM."""
    user = SimpleNamespace(id=OWNER, username="SotaThao", bot=False, is_self=False)
    data = dict(raw_text="/nailblog status", out=False, is_private=True, is_group=False, is_channel=False,
                sender_id=OWNER, chat_id=OWNER, fwd_from=None, message=SimpleNamespace(fwd_from=None),
                get_sender=AsyncMock(return_value=user), get_chat=AsyncMock(return_value=user), reply=AsyncMock())
    data.update(values)
    return SimpleNamespace(**data)
