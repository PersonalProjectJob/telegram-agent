"""Authenticated-user worker entry point; reads only its narrow subscription config."""
import argparse
from pathlib import Path
import time
import tomllib

from .config import _subscription
from .images.subscription import Worker, read


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args(argv)
    data = tomllib.loads(read(Path(args.config), owner=0, native=True).decode('utf-8'))
    if set(data) != {'subscription'}:
        parser.error('worker config must contain only subscription')
    cfg = _subscription(data['subscription'])
    worker = Worker(cfg)
    while True:
        worker.run_once()
        if args.once: return 0
        time.sleep(2)


if __name__ == '__main__':
    raise SystemExit(main())
