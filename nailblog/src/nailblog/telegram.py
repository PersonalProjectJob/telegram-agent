"""Dedicated Owner-only Telegram transport (/nailblog); no group processing."""

import asyncio
import getpass
import inspect
import logging
import os
import signal
from pathlib import Path

from ._errors import ConfigError
from ._runtime import ProcessLock
from .commands import make_owner_notice_sender, route_command, verify_owner
from .service import Service, wait_for_stop

LOG = logging.getLogger(__name__)

class SafeTransportHandler(logging.Handler):
    """Forward severity and exception identity without formatting library data."""

    def __init__(self):
        super().__init__(logging.WARNING)

    def emit(self, record):
        if record.levelno < logging.WARNING:
            return
        exception = record.exc_info[0].__name__ if record.exc_info and record.exc_info[0] else "none"
        LOG.log(record.levelno, "transport logger=%s level=%s exception=%s",
                record.name, record.levelname, exception)


def make_client(config):
    from telethon import TelegramClient

    try:
        api_id = int(os.environ['TG_API_ID'])
        api_hash = os.environ['TG_API_HASH'].strip()
        if api_id <= 0 or not api_hash:
            raise ValueError()
    except (KeyError, ValueError):
        raise ConfigError('TG_API_ID and TG_API_HASH are required') from None
    required = {'connection_retries', 'auto_reconnect', 'catch_up', 'sequential_updates'}
    if not required <= set(inspect.signature(TelegramClient).parameters):
        raise RuntimeError('Installed Telegram client lacks required reconnect options')
    session = Path(config.telegram_session)
    session.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    transport_log = logging.getLogger('nailblog.transport')
    transport_log.handlers = [SafeTransportHandler()]
    transport_log.setLevel(logging.WARNING)
    transport_log.propagate = False
    library_log = logging.getLogger('telethon')
    library_log.handlers = [SafeTransportHandler()]
    library_log.setLevel(logging.WARNING)
    library_log.propagate = False
    return TelegramClient(str(session), api_id, api_hash, connection_retries=-1,
                          auto_reconnect=True, catch_up=True, sequential_updates=True,
                          base_logger=transport_log)


async def login_telegram(config):
    directory=Path(config.telegram_session).parent
    directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    with ProcessLock(directory):
        await _login_locked(config)


async def _login_locked(config):
    client = make_client(config)
    try:
        await client.start(
            phone=lambda: getpass.getpass("Phone number: "),
            code_callback=lambda: getpass.getpass("Telegram login code: "),
            password=lambda: getpass.getpass("Telegram 2FA password: "))
    finally:
        try:
            await client.disconnect()
        finally:
            path = Path(config.telegram_session)
            if path.suffix != ".session":
                path = Path(str(path) + ".session")
            if path.exists():
                path.chmod(0o600)


def install_signal_handlers(stop, *, loop=None):
    loop = asyncio.get_running_loop() if loop is None else loop
    installed = []
    for signum in (signal.SIGTERM, signal.SIGINT):
        original = signal.getsignal(signum)
        try:
            loop.add_signal_handler(signum, stop.set)
            via_loop = True
        except (NotImplementedError, RuntimeError):
            signal.signal(signum, lambda *_: loop.call_soon_threadsafe(stop.set))
            via_loop = False
        installed.append((signum, original, via_loop))

    def cleanup():
        for signum, original, via_loop in installed:
            if via_loop:
                loop.remove_signal_handler(signum)
            signal.signal(signum, original)

    return cleanup


async def graceful_shutdown(client, worker_task, *, timeout=120, force_exit=None):
    timed_out = False
    try:
        if worker_task is not None:
            await asyncio.wait_for(asyncio.shield(worker_task), timeout=timeout)
    except TimeoutError:
        timed_out = True
        LOG.error("Shutdown deadline exceeded exception=TimeoutError")
    finally:
        try:
            await client.disconnect()
        finally:
            if timed_out:
                # Python cannot safely terminate a running thread. A process exit
                # keeps its durable state intact and avoids closing SQLite beneath
                # an active HTTP call or waiting indefinitely in executor shutdown.
                (os._exit if force_exit is None else force_exit)(1)
                raise SystemExit(1)  # Also covers injected test exit functions.


def make_handler(service, owner_ids):
    async def handler(event):
        await route_command(event, service, owner_ids)
    return handler


async def run(config):
    directory=Path(config.telegram_session).parent
    directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    with ProcessLock(directory):
        await _run_locked(config)


async def _run_locked(config):
    from telethon import events, errors

    client = make_client(config)
    service = Service(config)
    stop = asyncio.Event()
    verified_owner_ids = []
    cleanup_signals = lambda: None
    tasks = []
    scheduler_task = None
    handler = make_handler(service, verified_owner_ids)

    async def initialize():
        try:
            if not config.owner_ids:
                raise ConfigError('Owner IDs are required before startup')
            await client.connect()
            me = await client.get_me()
            if me is None or getattr(me, 'bot', False):
                raise ConfigError('Human Telegram login required: run login-telegram')
            while not stop.is_set():
                try:
                    selected = await verify_owner(client, config, me)
                    break
                except errors.FloodWaitError as error:
                    seconds = min(900, max(0, error.seconds))
                    LOG.warning('NailBlog Owner verification rate limited; wait_seconds=%s', seconds)
                    if await wait_for_stop(stop, seconds):
                        return
            if stop.is_set():
                return
            if not selected:
                raise RuntimeError('Owner binding is unverified')
            sender = make_owner_notice_sender(client, selected)
            service.notice_send = sender
            service.notice_owner_id = selected[0]
            verified_owner_ids.extend(selected)
        except ConfigError:
            LOG.error('NailBlog startup authentication/configuration invalid; operator action required')
            raise
        except Exception:
            LOG.error('NailBlog Owner verification failed; startup will retry')
            raise RuntimeError('NailBlog Owner verification failed') from None

    try:
        cleanup_signals = install_signal_handlers(stop)
        client.add_event_handler(handler, events.NewMessage())
        stopper = asyncio.create_task(stop.wait())
        startup = asyncio.create_task(initialize())
        tasks = [stopper, startup]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        if stopper in done:
            startup.cancel()
            await asyncio.gather(startup, return_exceptions=True)
            return
        startup.result()
        tasks = [asyncio.create_task(client.run_until_disconnected()), stopper]
        scheduler_task = asyncio.create_task(service.scheduler(stop))
        tasks.append(scheduler_task)
        await asyncio.sleep(0)
        if scheduler_task.done():
            scheduler_task.result()
            raise RuntimeError('NailBlog scheduler stopped during startup')
        from .receipt import ready
        ready(config)
        LOG.info('NailBlog ready owner_verified=1')
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        if stopper in done:
            return
        for task in done:
            task.result()
        raise RuntimeError('Telegram client disconnected or scheduler stopped')
    finally:
        stop.set()
        try:
            await graceful_shutdown(client, scheduler_task)
        finally:
            client.remove_event_handler(handler)
            cleanup_signals()
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
