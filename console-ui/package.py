"""Build a portable console from current source bytes using an exact public allowlist."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'console-ui'/'backend'))
from agentfleet_console.contracts import ContractError, check_archive, check_source

PREFIX = 'agentfleet-console/'
MANIFEST = 'package-manifest.json'
SCHEMA = 'agentfleet.source-package/v1'
STATIC_FILES = (
    'README.md', 'start-console.cmd', 'start-console.sh',
    'console-ui/run.py', 'console-ui/package.py',
    'console-ui/frontend/index.html',
    'console-ui/frontend/src/app.js', 'console-ui/frontend/src/api.js',
    'console-ui/frontend/src/state.js', 'console-ui/frontend/src/schema-form.js',
    'console-ui/frontend/assets/styles.css', 'console-ui/frontend/assets/tokens.css',
    'console-ui/backend/agentfleet_console/__init__.py',
    'console-ui/backend/agentfleet_console/paths.py',
    'console-ui/backend/agentfleet_console/store.py',
    'console-ui/backend/agentfleet_console/server.py',
    'console-ui/backend/agentfleet_console/cli.py',
    'console-ui/backend/agentfleet_console/errors.py',
    'console-ui/backend/agentfleet_console/registry.py',
    'console-ui/backend/agentfleet_console/schema.py',
    'console-ui/backend/agentfleet_console/compatibility.py',
    'console-ui/backend/agentfleet_console/policy.py',
    'console-ui/backend/agentfleet_console/worker_sources.py',
    'console-ui/backend/agentfleet_console/contracts.py',
    'console-ui/backend/agentfleet_console/workers/contract-review.json',
    'console-ui/scripts/check-contracts.py',
    'console-ui/backend/agentfleet_console/workers/__init__.py',
    'console-ui/backend/agentfleet_console/workers/adapters.py',
    'console-ui/backend/agentfleet_console/workers/descriptors/triage.json',
    'console-ui/backend/agentfleet_console/workers/descriptors/nailhub.json',
    'console-ui/backend/agentfleet_console/workers/compatibility.json',
    'console-ui/README.md',
    'config/listener.example.toml', 'config/nailhub.example.toml',
    'listener/prompts/classify.md', 'listener/prompts/case.md', 'listener/prompts/topics.md',
    'listener/prompts/followup.md',
)
TRIAGE_MODULES = (
    '__init__', '__main__', 'ai_transport', 'attachments', 'classifier', 'config',
    'followup_model', 'followup_store', 'followups', 'github_issues', 'group_notices', 'issue_format', 'model_lock', 'models',
    'pipeline', 'provider', 'reactions', 'receipt', 'rules', 'runtime', 'store', 'telegram_io', 'usage',
)
NAILHUB_MODULES = (
    '__init__', '__main__', '_errors', '_runtime', '_text', 'ai_transport', 'browser',
    'categories', 'commands', 'config', 'credentials', 'diagnostics', 'model', 'model_lock',
    'notices', 'publication', 'receipt', 'service', 'state', 'telegram', 'validation',
)
FILES = STATIC_FILES + tuple('listener/src/tg_triage/'+name+'.py' for name in TRIAGE_MODULES) + tuple(
    'nailhub/src/nailhub/'+name+'.py' for name in NAILHUB_MODULES)
MAX_FILE_BYTES = 8 * 1024 * 1024
PACKAGE_REGISTRATIONS = {
    'triage': {'descriptor':'console-ui/backend/agentfleet_console/workers/descriptors/triage.json',
               'adapter':'console-ui/backend/agentfleet_console/workers/adapters.py'},
    'nailhub': {'descriptor':'console-ui/backend/agentfleet_console/workers/descriptors/nailhub.json',
                'adapter':'console-ui/backend/agentfleet_console/workers/adapters.py'},
}
LOCAL_PACKAGES = {
    'agentfleet_console':'console-ui/backend/agentfleet_console',
    'tg_triage':'listener/src/tg_triage',
    'nailhub':'nailhub/src/nailhub',
}


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


def validate_closure(contents):
    """Require explicit registered assets and local Python imports; never execute workers."""
    modules = {}
    entrypoints = []
    for relative, data in contents.items():
        if not relative.endswith('.py'):
            continue
        for package, directory in LOCAL_PACKAGES.items():
            if relative.startswith(directory+'/'):
                suffix = relative[len(directory)+1:-3].replace('/', '.')
                name = package + ('.'+suffix if suffix != '__init__' else '')
                if name.endswith('.__init__'):
                    name = name[:-9]
                try:
                    modules[name] = (relative, ast.parse(data.decode('utf-8-sig'), filename=relative))
                except (UnicodeError, SyntaxError, ValueError):
                    raise PackageError('Invalid packaged Python source: '+relative) from None
                break
        else:
            try:
                entrypoints.append((relative, ast.parse(data.decode('utf-8-sig'), filename=relative)))
            except (UnicodeError, SyntaxError, ValueError):
                raise PackageError('Invalid packaged Python source: '+relative) from None
    required = 'agentfleet_console.workers.adapters'
    if required not in modules:
        raise PackageError('Registered adapter source is missing from package.')
    functions = [node for node in modules[required][1].body if isinstance(node, ast.FunctionDef) and node.name == 'builtins']
    returns = [node.value for function in functions for node in function.body if isinstance(node, ast.Return)]
    if len(returns) != 1 or not isinstance(returns[0], ast.Dict) or any(not isinstance(key, ast.Constant) or not isinstance(key.value, str) for key in returns[0].keys):
        raise PackageError('Portable worker registrations must be explicit source dictionary entries.')
    registered = [key.value for key in returns[0].keys]
    if len(registered) != len(set(registered)) or set(registered) != set(PACKAGE_REGISTRATIONS):
        raise PackageError('Worker registration requires explicit packaging metadata and allowlist review.')
    for worker, registration in PACKAGE_REGISTRATIONS.items():
        if set(registration) != {'descriptor','adapter'} or any(path not in contents for path in registration.values()):
            raise PackageError('Registered worker descriptor or adapter is missing from package: '+worker)
        try:
            descriptor = json.loads(contents[registration['descriptor']])
        except (ValueError, UnicodeError):
            raise PackageError('Invalid packaged worker descriptor: '+worker) from None
        if not isinstance(descriptor, dict) or descriptor.get('id') != worker:
            raise PackageError('Packaged worker descriptor registration mismatch: '+worker)

    def require(name, importer):
        if name.split('.', 1)[0] not in LOCAL_PACKAGES:
            return
        parts = name.split('.')
        for length in range(1, len(parts)+1):
            dependency = '.'.join(parts[:length])
            if dependency not in modules:
                raise PackageError('Local import is missing from package: '+dependency+' (from '+importer+')')

    def import_base(name, relative, node):
        package = name if relative.endswith('/__init__.py') else name.rpartition('.')[0]
        if not node.level:
            return node.module or ''
        parts = package.split('.')
        if node.level > len(parts):
            raise PackageError('Invalid relative local import: '+relative)
        return '.'.join(parts[:len(parts)-node.level+1] + (node.module.split('.') if node.module else []))

    def exported(name, symbol, trail=frozenset()):
        """An import alias is an export only if its origin resolves; cycles prove nothing."""
        if name.split('.', 1)[0] not in LOCAL_PACKAGES:
            return True  # This gate checks bundled local imports, not external packages.
        token = (name, symbol)
        if token in trail or name not in modules:
            return False
        relative, tree = modules[name]
        trail = trail | {token}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.name == symbol:
                    return True
            elif isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == symbol for target in node.targets):
                return True
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == symbol:
                return True
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if (alias.asname or alias.name.split('.')[0]) == symbol:
                        return alias.name.split('.', 1)[0] not in LOCAL_PACKAGES or alias.name in modules
            elif isinstance(node, ast.ImportFrom):
                origin = import_base(name, relative, node)
                for alias in node.names:
                    if (alias.asname or alias.name) != symbol:
                        continue
                    if origin+'.'+alias.name in modules or exported(origin, alias.name, trail):
                        return True
        return False

    trees = [(name, relative, tree) for name, (relative, tree) in modules.items()]
    trees += [('', relative, tree) for relative, tree in entrypoints]
    for name, relative, tree in trees:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    require(alias.name, relative)
            elif isinstance(node, ast.ImportFrom):
                base = import_base(name, relative, node)
                require(base, relative)
                base_module = modules.get(base)
                if base_module and base_module[0].endswith('/__init__.py'):
                    for alias in node.names:
                        if not exported(base, alias.name):
                            require(base+'.'+alias.name, relative)
    return {'registeredWorkers':len(registered), 'localModules':len(modules)}


def validate_contract_archive(contents):
    try:
        check_archive(contents)
    except ContractError as error:
        raise PackageError(str(error)) from None


def build(output, *, root=ROOT):
    root = Path(root).resolve()
    target = output_path(root, output)
    try:
        check_source(root)
    except ContractError as error:
        raise PackageError(str(error)) from None
    if len(FILES) != len(set(FILES)):
        raise PackageError('Duplicate packaging allowlist entry.')
    contents = {relative: source_bytes(root, relative) for relative in sorted(FILES)}
    validate_closure(contents)
    validate_contract_archive(contents)
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
            contents = {}
            for relative, expected_file in manifest['files'].items():
                data = archive.read(PREFIX+relative)
                if expected_file != {'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)}:
                    raise PackageError('Archive checksum mismatch: '+relative)
                contents[relative] = data
            validate_closure(contents)
            validate_contract_archive(contents)
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
