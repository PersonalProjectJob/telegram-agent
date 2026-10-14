"""Local-only profile bootstrap and read-only probes; no live mutation CLI."""

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
from ._runtime import ProcessLock

from ._errors import ConfigError
from .browser import Browser
from .config import load_config
from .service import Service
from .validation import NeedsHuman


async def execute(command, config):
    if command in ('run', 'login-telegram'):
        from .telegram import run, login_telegram
        await (run(config) if command == 'run' else login_telegram(config))
        return
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
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog='python -m nailhub')
    parser.add_argument('command', choices=('run', 'login-telegram', 'login', 'probe', 'preview', 'status'))
    parser.add_argument('--config', default=os.environ.get('NAILHUB_CONFIG', '/etc/nailhub/nailhub.toml'))
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        Path(config.db_path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if args.command in ('run', 'login-telegram'):
            asyncio.run(execute(args.command, config))
        else:
            directory=Path(config.telegram_session).parent
            directory.mkdir(parents=True,exist_ok=True,mode=0o700)
            with ProcessLock(directory):
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
