"""Consume the NailHub namespace before triage; authorize from Telegram metadata."""

import re

NAMESPACE = re.compile(r"^\s*/nailhub(?:\b|@)", re.IGNORECASE)
COMMANDS = {"/nailhub scan": "scan", "/nailhub status": "status",
            "/nailhub pause": "pause", "/nailhub resume": "resume", "/nailhub pending": "pending"}


def make_owner_notice_sender(client, verified_owner_ids):
    """Use the active client only, after verify_owner returned its single numeric binding."""
    if len(verified_owner_ids) != 1 or type(verified_owner_ids[0]) is not int or verified_owner_ids[0] <= 0:
        raise ValueError('verified single Owner binding required')
    owner_id = verified_owner_ids[0]
    async def send(text):
        return await client.send_message(owner_id, text, parse_mode=None, link_preview=False)
    return send


async def verify_owner(client, config, me):
    """Bootstrap username only to verify the Owner's numeric binding, never to authorize a DM."""
    if len(config.owner_ids) != 1:
        return ()
    named = await client.get_entity(config.owner_username)
    numeric = await client.get_entity(config.owner_ids[0])
    selected = config.owner_ids[0]
    if (getattr(named, 'id', None) == selected == getattr(numeric, 'id', None)
            and getattr(named, 'bot', None) is False and getattr(numeric, 'bot', None) is False
            and getattr(named, 'username', '').casefold() == config.owner_username.casefold()
            and selected != getattr(me, 'id', None)):
        return (selected,)
    return ()


async def route_command(event, service, owner_ids):
    text = getattr(event, "raw_text", "") or ""
    if not NAMESPACE.match(text):
        return False
    # Any message in this namespace is consumed, including malformed and rejected commands.
    message = getattr(event, "message", None)
    sender, peer = getattr(event, "sender_id", None), getattr(event, "chat_id", None)
    if (getattr(event, "is_private", None) is not True or getattr(event, "is_group", False)
            or getattr(event, "is_channel", False) or getattr(event, "out", None) is not False
            or getattr(event, "fwd_from", None) is not None
            or getattr(message, "fwd_from", None) is not None
            or type(sender) is not int or type(peer) is not int or sender != peer
            or sender <= 0 or sender not in owner_ids):
        return True
    # Verify the actual peer and sender are human users (also excludes Saved Messages).
    try:
        user = await event.get_sender()
        chat = await event.get_chat()
        if (getattr(user, "id", None) != sender or getattr(chat, "id", None) != peer
                or getattr(user, "bot", None) is not False or getattr(chat, "bot", None) is not False
                or getattr(user, "is_self", False) or getattr(chat, "is_self", False)):
            return True
    except Exception:
        return True
    command = COMMANDS.get(text)
    if command is None:
        await event.reply("Commands: /nailhub scan | /nailhub status | /nailhub pending | /nailhub pause | /nailhub resume",
                          parse_mode=None, link_preview=False)
        return True
    response = service.command(command)
    for text in response if isinstance(response, list) else [response]:
        await event.reply(text, parse_mode=None, link_preview=False)
    return True
