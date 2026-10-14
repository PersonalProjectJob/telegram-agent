"""Local-only profile bootstrap and read-only probes; no live mutation CLI."""

import argparse
import asyncio
import json
import os
from pathlib import Path
from ..runtime import ProcessLock

from ..config import ConfigError
from .browser import Browser
from .config import load_config
from .service import Service
from .validation import NeedsHuman


async def execute(command, config):
    if command == 'status':
        print(Service(config).command('status'))
        return
    if command == 'preview':
        if config.mode != 'preview':
            raise NeedsHuman('preview CLI requires mode=preview')
        service = Service(config)
        print(await service.scan())
        print(service.command('status'))
        return
    async with Browser(config) as browser:
        if command == 'login':
            await browser.login()
            print('NailHub admin login verified; dedicated profile saved.')
        elif command == 'probe':
            candidates = await browser.candidates()
            # No post content or credential material in CLI diagnostics.
            print(json.dumps({'authenticated': True, 'draft_recruitment_candidates': len(candidates),
                              'contract_verified': config.verified, 'mode': config.mode,
                              'production_ready': False}))


def main(argv=None):
    os.umask(0o077)
    parser = argparse.ArgumentParser(prog='python -m tg_triage.nailhub')
    parser.add_argument('command', choices=('login', 'probe', 'preview', 'status'))
    parser.add_argument('--config', required=True)
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        Path(config.db_path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with ProcessLock(Path(config.db_path).parent):
            asyncio.run(execute(args.command, config))
        return 0
    except (ConfigError, NeedsHuman) as error:
        print(str(error))
        return 2
    except Exception as error:
        print('NailHub operation failed: ' + type(error).__name__)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
