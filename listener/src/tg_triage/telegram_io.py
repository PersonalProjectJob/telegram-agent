"""Lazy Telegram transport, one processing worker and interactive login."""

import asyncio
import getpass
import inspect
import logging
import os
import signal
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from . import reactions, rules
from .models import IncomingMessage
from .config import ConfigError

LOG = logging.getLogger(__name__)

SWEEP_SECONDS = 15  # how often the worker closes cases whose window expired
DOWNLOAD_TIMEOUT_SECONDS = 60


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
    from telethon import TelegramClient, connection

    # Confirm support in the installed release as well as the upstream reference:
    # https://docs.telethon.dev/en/stable/modules/client.html
    # Negative connection_retries means unlimited reconnect attempts in v1.
    required = {"connection_retries", "auto_reconnect", "catch_up", "sequential_updates"}
    if not required <= set(inspect.signature(TelegramClient).parameters):
        raise RuntimeError("Installed Telegram client does not support required reconnect options")
    path = Path(config.telegram.session_path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Child loggers reach this handler, but their original data never reach stdout.
    transport_log = logging.getLogger("tg_triage.transport")
    transport_log.handlers = [SafeTransportHandler()]
    transport_log.setLevel(logging.WARNING)
    transport_log.propagate = False
    library_log = logging.getLogger("telethon")
    library_log.handlers = [SafeTransportHandler()]
    library_log.setLevel(logging.WARNING)
    library_log.propagate = False
    options = dict(connection_retries=-1, auto_reconnect=True, catch_up=True,
                   sequential_updates=True, base_logger=transport_log)
    proxy = config.telegram.proxy
    if proxy.host:
        options.update(connection=connection.ConnectionTcpMTProxyRandomizedIntermediate,
                       proxy=(proxy.host, proxy.port, proxy.secret))
    return TelegramClient(str(path), config.api_id, config.api_hash, **options)


async def login(config):
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
            path = Path(config.telegram.session_path)
            if path.suffix != ".session":
                path = Path(str(path) + ".session")
            if path.exists():
                path.chmod(0o600)


def make_reaction_sender(client):
    """Bản triển khai Telethon của `send(chat_id, message_id, emoji)` cho reactions.py."""
    async def send(chat_id, message_id, emoji):
        from telethon.tl import functions, types

        await client(functions.messages.SendReactionRequest(
            peer=chat_id, msg_id=message_id,
            reaction=[types.ReactionEmoji(emoticon=emoji)]))

    return send


def _reply_target(event):
    """Message id this event replies to, ignoring the implicit forum-topic root."""
    target = getattr(event, "reply_to_msg_id", None)
    if type(target) is not int:
        return None
    reply_to = getattr(event, "reply_to", None)
    if getattr(reply_to, "forum_topic", False) and not getattr(reply_to, "reply_to_top_id", None):
        return None
    return target


def _mentions_listener(event) -> bool:
    """True when Telegram itself flags the message as tagging this account (12.11).

    Telethon sets `Message.mentioned` from the server's flag, so no username is ever guessed from
    the text. Only a real `True` counts: a missing attribute or a truthy stand-in is not a call.
    """
    return getattr(getattr(event, "message", None), "mentioned", False) is True


def _image_info(event):
    """(has_image, declared size, mime) for a photo or an image document; stickers excluded."""
    if getattr(event, "sticker", None):
        return False, None, None
    file = getattr(event, "file", None)
    size = getattr(file, "size", None)
    size = size if type(size) is int else None
    if getattr(event, "photo", None):
        return True, size, None
    mime = getattr(file, "mime_type", None)
    if getattr(event, "document", None) and isinstance(mime, str) and mime.startswith("image/"):
        return True, size, mime
    return False, None, None


def make_media_downloader(client, loop, *, timeout=DOWNLOAD_TIMEOUT_SECONDS):
    """Synchronous `download(chat_id, message_id, directory)` for the pipeline thread.

    Telethon is bound to the event loop, so the coroutine runs there while the worker
    thread waits. Returns the saved path (Telethon picks the name inside `directory`)
    or None when the message has no media.
    """
    def download(chat_id, message_id, directory):
        async def fetch():
            target = await client.get_messages(chat_id, ids=message_id)
            if target is None or not getattr(target, "media", None):
                return None
            return await client.download_media(target, file=str(directory))

        future = asyncio.run_coroutine_threadsafe(fetch(), loop)
        try:
            path = future.result(timeout)
        except BaseException:
            future.cancel()
            raise
        return Path(path) if path else None

    return download


async def enqueue_event(event, queue, chat_ids, store, *, stop=None, react=None,
                        listen_all_groups=False):
    if event.out or event.is_private:
        return
    if listen_all_groups:
        # is_group: basic group và supergroup; kênh broadcast thì False/None.
        if not event.is_group:
            return
    elif event.chat_id not in chat_ids:
        return
    # Persist content before metadata lookups, which may await network responses.
    has_image, image_size, image_mime = _image_info(event)
    sender_id = getattr(event, "sender_id", None)
    message = IncomingMessage(event.chat_id, event.id, event.date or datetime.now(timezone.utc),
                              "không rõ người gửi", event.raw_text or "",
                              sender_id=sender_id if type(sender_id) is int else None,
                              reply_to_id=_reply_target(event),
                              is_forward=getattr(event, "fwd_from", None) is not None,
                              has_image=has_image, image_size=image_size, image_mime=image_mime,
                              mentions_listener=_mentions_listener(event))
    if not store.enqueue(message):
        return
    if stop is not None and stop.is_set():
        return  # Preserve catch-up updates during shutdown without metadata waits.
    if react is not None and rules.is_command(message.text):
        # Chỉ tin mới (enqueue True); lỗi reaction đã được react() nuốt và log.
        if await reactions.react(react, message.chat_id, message.message_id, reactions.RECEIVED):
            _mark(store, message.chat_id, message.message_id, "received")
    try:
        sender = await event.get_sender()
    except Exception as error:
        LOG.warning("Sender lookup failed exception=%s", type(error).__name__)
        sender = None
    try:
        chat = await event.get_chat()
    except Exception as error:
        LOG.warning("Chat lookup failed exception=%s", type(error).__name__)
        chat = None
    name = " ".join(value for value in (
        getattr(sender, "first_name", None), getattr(sender, "last_name", None)) if value)
    name = name or getattr(sender, "title", None) or "không rõ người gửi"
    message = replace(message, sender_name=name, chat_username=getattr(chat, "username", None))
    store.update_metadata(message)
    if stop is not None and stop.is_set():
        return  # Durable queued data will be replayed next time.
    await queue.put(message)
    if queue.qsize() > 20:
        LOG.warning("Queue backlog queue_length=%s", queue.qsize())


_TICK = object()


async def _next_message(queue, stop, timeout=None):
    """Next queued message, None when stopping, or `_TICK` when `timeout` elapsed idle."""
    if stop is None:
        if timeout is None:
            return await queue.get()
        try:
            return await asyncio.wait_for(queue.get(), timeout)
        except TimeoutError:
            return _TICK
    getter = asyncio.create_task(queue.get())
    stopper = asyncio.create_task(stop.wait())
    try:
        await asyncio.wait((getter, stopper), timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        if stop.is_set():
            if getter.done() and not getter.cancelled():
                queue.task_done()
            return None
        if not getter.done():
            return _TICK
        return getter.result()
    finally:
        for task in (getter, stopper):
            if not task.done():
                task.cancel()
        await asyncio.gather(getter, stopper, return_exceptions=True)


async def _in_thread(function, *arguments):
    """Run blocking pipeline work in a thread and always let it finish (F13)."""
    task = asyncio.create_task(asyncio.to_thread(function, *arguments))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # Finish the active thread before callers close SQLite on shutdown.
        await task
        raise


def _mark(store, chat_id, message_id, kind):
    """Remember a placed reaction; a storage failure must never reach the caller."""
    try:
        store.mark_reaction(chat_id, message_id, kind)
    except Exception as error:
        LOG.warning("Reaction state failed chat_id=%s message_id=%s exception=%s",
                    chat_id, message_id, type(error).__name__)


def _already(store, chat_id, message_id, kind):
    check = getattr(store, "reaction_done", None)
    if check is None:
        return False
    try:
        return bool(check(chat_id, message_id, kind))
    except Exception as error:
        LOG.warning("Reaction state failed chat_id=%s message_id=%s exception=%s",
                    chat_id, message_id, type(error).__name__)
        return False


def make_reactor(send, loop, *, timeout=reactions.TIMEOUT_SECONDS + 5):
    """Blocking `reactor(chat_id, message_id, emoji) -> bool` for the pipeline thread.

    Telethon is bound to the event loop, so the reaction runs there while the worker thread
    waits (bounded). Failures are logged by `reactions.react`; here only the wait can fail.
    """
    def reactor(chat_id, message_id, emoji):
        future = asyncio.run_coroutine_threadsafe(
            reactions.react(send, chat_id, message_id, emoji), loop)
        try:
            return bool(future.result(timeout))
        except Exception as error:
            future.cancel()
            LOG.warning("Reaction wait failed chat_id=%s message_id=%s exception=%s",
                        chat_id, message_id, type(error).__name__)
            return False

    return reactor


async def _react_issue_created(pipeline, react, decision, chat_id, message_id):
    """OK hand on the first message of every group this call created an issue for, only live.

    A closed window can create several issues (12.9); `reaction_message_ids` lists each group's
    first message. Older single-issue decisions fall back to `reaction_message_id`.
    """
    if (react is not None and pipeline.config.mode == "live"
            and getattr(decision, "issue_number", None) is not None):
        targets = tuple(getattr(decision, "reaction_message_ids", None) or ()) or (
            getattr(decision, "reaction_message_id", None) or message_id,)
        store = getattr(pipeline, "store", None)
        for target in dict.fromkeys(targets):
            if _already(store, chat_id, target, "ok"):
                continue   # one OK hand per message, also across restarts (12.10)
            if await reactions.react(react, chat_id, target, reactions.ISSUE_CREATED):
                _mark(store, chat_id, target, "ok")


async def worker(queue, pipeline, stop=None, *, react=None):
    close_due = getattr(pipeline, "close_due_cases", None)
    interval = SWEEP_SECONDS if close_due is not None else None
    attach = getattr(pipeline, "set_reactor", None)
    if attach is not None and react is not None:
        # 12.10: the pipeline thread asks the event loop for the heart that precedes each issue.
        attach(make_reactor(react, asyncio.get_running_loop()))
    try:
        await _work(queue, pipeline, stop, react, interval)
    finally:
        if attach is not None and react is not None:
            attach(None)


async def _work(queue, pipeline, stop, react, interval):
    while stop is None or not stop.is_set():
        message = await _next_message(queue, stop, interval)
        if message is None:
            return
        if message is _TICK:
            for decision in await _in_thread(_sweep_cases, pipeline):
                if decision.reaction_message_id is not None and decision.case_key:
                    chat_id = int(decision.case_key.rsplit(":", 1)[0])
                    await _react_issue_created(pipeline, react, decision, chat_id,
                                               decision.reaction_message_id)
            continue
        try:
            decision = await _in_thread(_process_message, pipeline, message)
            # Reaction chạy ở event loop (không gọi Telethon từ thread phụ), sau khi
            # pipeline đã ghi kết quả; dry-run không bao giờ có 👌.
            await _react_issue_created(pipeline, react, decision, message.chat_id,
                                       message.message_id)
        finally:
            queue.task_done()


def _sweep_cases(pipeline):
    """Close expired cases; a failure here must never stop the worker."""
    try:
        return pipeline.close_due_cases()
    except Exception as error:
        LOG.error("Case sweep failed exception=%s", type(error).__name__)
        return []


def _process_message(pipeline, message):
    """Contain every per-message failure, including rules and final persistence.

    Trả Decision (hoặc None nếu trùng/lỗi) để worker async quyết định reaction.
    """
    try:
        return pipeline.process(message)
    except Exception as error:
        LOG.error("Message failed chat_id=%s message_id=%s exception=%s",
                  message.chat_id, message.message_id, type(error).__name__)
        try:
            pipeline.store.fail_message(message, type(error).__name__)
        except Exception as storage_error:
            # A broken disk may prevent quarantine. Recovery's attempt cap still
            # bounds replay; one row must not terminate the processing worker.
            LOG.error("Message quarantine failed exception=%s", type(storage_error).__name__)
        return None


async def heartbeat(queue, interval=600):
    while True:
        await asyncio.sleep(interval)
        LOG.info("alive queue_length=%s", queue.qsize())


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


async def run(config, pipeline):
    from telethon import events

    client = make_client(config)
    react = make_reaction_sender(client)
    queue = asyncio.Queue()
    stop = asyncio.Event()
    cleanup_signals = lambda: None

    async def handler(event):
        await enqueue_event(event, queue, config.telegram.chat_ids, pipeline.store, stop=stop,
                            react=react, listen_all_groups=config.telegram.listen_all_groups)

    tasks = []
    worker_task = None

    async def initialize():
        await client.connect()
        me = await client.get_me()
        if me is None:
            raise ConfigError("chưa đăng nhập: chạy lệnh login")
        if getattr(me, "bot", False):
            raise ConfigError("không hỗ trợ tài khoản bot")

    try:
        cleanup_signals = install_signal_handlers(stop)
        for message in pipeline.store.queued_messages():
            if config.telegram.listen_all_groups or message.chat_id in config.telegram.chat_ids:
                queue.put_nowait(message)
        # listen_all_groups: không lọc theo chat ở Telethon; enqueue_event lọc is_group.
        builder = (events.NewMessage() if config.telegram.listen_all_groups
                   else events.NewMessage(chats=list(config.telegram.chat_ids)))
        client.add_event_handler(handler, builder)
        stopper = asyncio.create_task(stop.wait())
        startup = asyncio.create_task(initialize())
        tasks = [stopper, startup]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        if stopper in done:
            startup.cancel()
            await asyncio.gather(startup, return_exceptions=True)
            return
        startup.result()
        attach_downloader = getattr(pipeline, "set_media_downloader", None)
        if attach_downloader is not None:
            attach_downloader(make_media_downloader(client, asyncio.get_running_loop()))
        worker_task = asyncio.create_task(worker(queue, pipeline, stop, react=react))
        tasks = [worker_task, asyncio.create_task(heartbeat(queue)),
                 asyncio.create_task(client.run_until_disconnected()), stopper]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        if stopper in done:
            return
        for task in done:
            task.result()
        raise RuntimeError("Telegram client disconnected or processing worker stopped")
    finally:
        stop.set()
        try:
            await graceful_shutdown(client, worker_task)
        finally:
            remove_handler = getattr(client, "remove_event_handler", None)
            if remove_handler:
                remove_handler(handler)
            cleanup_signals()
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
