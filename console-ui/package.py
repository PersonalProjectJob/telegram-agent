"""Build a portable console from current source bytes using an exact public allowlist."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PREFIX = 'agentfleet-console/'
MANIFEST = 'package-manifest.json'
SCHEMA = 'agentfleet.source-package/v1'
STATIC_FILES = (
    'README.md', 'start-console.cmd', 'start-console.sh',
    'console-ui/run.py', 'console-ui/backend.py', 'console-ui/package.py',
    'console-ui/index.html', 'console-ui/app.js', 'console-ui/api.js',
    'console-ui/styles.css', 'console-ui/tokens.css', 'console-ui/README.md',
    'config/listener.example.toml', 'config/nailhub.example.toml',
    'listener/prompts/classify.md', 'listener/prompts/case.md', 'listener/prompts/topics.md',
)
TRIAGE_MODULES = (
    '__init__', '__main__', 'ai_transport', 'attachments', 'classifier', 'config',
    'github_issues', 'group_notices', 'issue_format', 'model_lock', 'models',
    'pipeline', 'provider', 'reactions', 'receipt', 'rules', 'runtime', 'store', 'telegram_io',
)
NAILHUB_MODULES = (
    '__init__', '__main__', '_errors', '_runtime', '_text', 'ai_transport', 'browser',
    'categories', 'commands', 'config', 'credentials', 'diagnostics', 'model', 'model_lock',
    'notices', 'publication', 'receipt', 'service', 'state', 'telegram', 'validation',
)
FILES = STATIC_FILES + tuple('listener/src/tg_triage/'+name+'.py' for name in TRIAGE_MODULES) + tuple(
    'nailhub/src/nailhub/'+name+'.py' for name in NAILHUB_MODULES)
MAX_FILE_BYTES = 8 * 1024 * 1024


class PackageError(ValueError):
    """A safe packaging error; never include file contents in diagnostics."""


def reject_link(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400):
        raise PackageError('Symlinks and reparse points are not allowed: '+str(path))


def source_bytes(root, relative):
    pure = PurePosixPath(relative)
    if pure.is_absolute() or any(part in ('', '.', '..') for part in pure.parts) or '\\' in relative or ':' in relative:
        raise PackageError('Invalid allowlist path.')
    path = root
    for part in pure.parts:
        path = path / part
        reject_link(path)
    if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
        raise PackageError('Required public source file is missing or too large: '+relative)
    return path.read_bytes()


def output_path(root, output):
    path = Path(os.path.abspath(output))
    for parent in (*reversed(path.parents), path):
        reject_link(parent)
    if path.suffix.lower() != '.zip':
        raise PackageError('Output must be a .zip file.')
    if path == root or root in path.parents and root/'dist' not in path.parents:
        raise PackageError('Within the source folder, output must be inside dist/.')
    if path.exists():
        raise PackageError('Output already exists; choose a new archive filename.')
    return path


def entry(relative, data):
    item = zipfile.ZipInfo(PREFIX+relative, date_time=(1980, 1, 1, 0, 0, 0))
    item.create_system = 3
    item.external_attr = (stat.S_IFREG | (0o755 if relative == 'start-console.sh' else 0o644)) << 16
    item.compress_type = zipfile.ZIP_DEFLATED
    return item, data


def build(output, *, root=ROOT):
    root = Path(root).resolve()
    target = output_path(root, output)
    contents = {relative: source_bytes(root, relative) for relative in sorted(FILES)}
    manifest = {
        'schema': SCHEMA, 'owner': 'AlexChu', 'entrypoint': 'console-ui/run.py',
        'python': '>=3.11', 'consoleDependencies': 'standard-library-only',
        'scope': 'local console and matching runtime source; no installed services or credentials',
        'files': {relative: {'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)}
                  for relative, data in contents.items()},
    }
    contents[MANIFEST] = (json.dumps(manifest, indent=2, sort_keys=True)+'\n').encode()
    target.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        # Exclusive creation prevents accidental overwrite, including an output-file race.
        with target.open('xb') as stream:
            created = True
            with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
                for relative, data in sorted(contents.items()):
                    item, data = entry(relative, data)
                    archive.writestr(item, data)
        verify(target)
    except BaseException:
        if created:
            target.unlink(missing_ok=True)
        raise
    return {'path': str(target), 'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
            'files': len(contents), 'bytes': target.stat().st_size}


def verify(path):
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            expected = {PREFIX+relative for relative in FILES} | {PREFIX+MANIFEST}
            if len(names) != len(set(names)) or set(names) != expected:
                raise PackageError('Archive has unexpected, duplicate or missing files.')
            for info in archive.infolist():
                if info.flag_bits & 1 or info.file_size > MAX_FILE_BYTES or stat.S_IFMT(info.external_attr >> 16) != stat.S_IFREG:
                    raise PackageError('Archive contains an oversized or non-regular entry.')
            manifest = json.loads(archive.read(PREFIX+MANIFEST))
            if not isinstance(manifest, dict) or manifest.get('schema') != SCHEMA or not isinstance(manifest.get('files'), dict) or set(manifest['files']) != set(FILES):
                raise PackageError('Archive manifest is invalid.')
            for relative, expected_file in manifest['files'].items():
                data = archive.read(PREFIX+relative)
                if expected_file != {'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)}:
                    raise PackageError('Archive checksum mismatch: '+relative)
            return manifest
    except PackageError:
        raise
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, zipfile.BadZipFile):
        raise PackageError('Cannot read a valid source archive.') from None


def main():
    parser = argparse.ArgumentParser(description='Package current AgentFleet console source; no Git or npm required.')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--output', type=Path, default=None, help='New ZIP path; default dist/agentfleet-console.zip')
    group.add_argument('--verify', type=Path, help='Check the exact allowlist and SHA256 manifest of an archive')
    args = parser.parse_args()
    try:
        if args.verify:
            manifest = verify(args.verify)
            print('Verified '+str(len(manifest['files']))+' source files.')
        else:
            result = build(args.output or ROOT/'dist'/'agentfleet-console.zip')
            print(json.dumps(result, indent=2))
    except (PackageError, OSError) as error:
        parser.exit(2, 'Package failed: '+str(error)+'\n')


if __name__ == '__main__':
    main()
