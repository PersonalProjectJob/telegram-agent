"""Prepare a new, validated Agy-only intermediate TOML; never replace either input or output.

Run with the reviewed source on PYTHONPATH (nailblog/src), before installing its wheel.
This prepares configuration only: no database, website, environment or service changes.
"""
import argparse
import copy
import json
import os
import tempfile
import tomllib
from pathlib import Path

from nailblog.config import AGY_SLOTS, REMOVED_KEYS, REMOVED_TABLES, load_config


def intermediate(data):
    data = copy.deepcopy(data)
    for name in REMOVED_TABLES:
        data.pop(name, None)
    for section, keys in REMOVED_KEYS.items():
        table = data.get(section, {})
        if not isinstance(table, dict):
            raise ValueError(f'{section} must be a table')
        for key in keys:
            table.pop(key, None)
    # Preview is deliberate: do not invent a review_bypass grant or enable writes during migration.
    data.setdefault('service', {}).update(mode='preview', slots=list(AGY_SLOTS))
    data.setdefault('text_model', {})['provider'] = 'agy'
    data.setdefault('image', {})['provider'] = 'subscription'
    data.setdefault('admin', {}).pop('category_ids', None)
    data['admin']['daily_publish_limit'] = len(AGY_SLOTS)
    data.setdefault('autoflow', {})['enabled'] = False
    # The previous two-slot map cannot describe the four-slot schedule; let the loader supply its defaults.
    data.setdefault('daily', {}).pop('slot_categories', None)
    return data


def toml_text(data):
    """Serialize the TOML primitives used by config.py; unsupported values fail instead of being lost."""
    def value(item):
        if isinstance(item, str):
            return json.dumps(item, ensure_ascii=False)
        if type(item) is bool:
            return str(item).lower()
        if type(item) in (int, float):
            return repr(item)
        if isinstance(item, list):
            return '[' + ', '.join(value(v) for v in item) + ']'
        raise ValueError('unsupported TOML value')

    lines = ['# Validated intermediate: preview mode, autoflow disabled; Owner must review before live.']

    def table(node, trail=()):
        if trail:
            lines.append('[' + '.'.join(json.dumps(k) for k in trail) + ']')
        for key, item in node.items():
            if not isinstance(item, dict):
                lines.append(json.dumps(key) + ' = ' + value(item))
        for key, item in node.items():
            if isinstance(item, dict):
                lines.append('')
                table(item, (*trail, key))
    table(data)
    return '\n'.join(lines) + '\n'


def migrate(source, destination):
    source, destination = Path(source), Path(destination)
    if source.resolve() == destination.resolve() or destination.exists() or destination.is_symlink():
        raise FileExistsError('output must be a new path, different from input')
    text = toml_text(intermediate(tomllib.loads(source.read_text(encoding='utf-8'))))
    # Validate with the REAL reviewed loader before publishing any output. No replacement on validation failure.
    with tempfile.TemporaryDirectory(prefix='nailblog-config-') as directory:
        os.chmod(directory, 0o700)
        candidate = Path(directory) / 'intermediate.toml'
        candidate.write_text(text, encoding='utf-8', newline='\n')
        load_config(candidate)
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
        stream.write(text)
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--in', dest='source', required=True, type=Path)
    parser.add_argument('--out', dest='destination', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        migrate(args.source, args.destination)
    except (OSError, ValueError) as error:
        # Do not echo config values or exception text, which may contain private input.
        parser.exit(1, 'migration refused: ' + type(error).__name__ + '\n')
    print('Validated intermediate written; preview mode, autoflow disabled. Review before installation.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
