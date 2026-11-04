"""Standalone optional Telegram bot; no shared Telegram session or NailBlog state.

The credential file and service are not provisioned by this module. Bot polling
requires one exclusive process, and commands remain subject to the IPC gates.
"""
try:
    import fcntl
except ImportError:
    fcntl=None
import json
import os
import re
import stat
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPRedirectHandler
from .telegram_commands import submit, call


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl): return None


def load_config(path):
    path=Path(path)
    if not path.is_absolute(): raise ValueError('invalid configuration path')
    for component in (path,*path.parents):
        info=component.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid!=0 or info.st_mode & 0o022:
            raise ValueError('untrusted configuration')
    info=path.stat()
    if (not path.is_file() or info.st_size>4096 or info.st_mode & 0o007
            or (info.st_mode & 0o040 and info.st_gid != os.getgid())): raise ValueError('invalid configuration')
    config=json.loads(path.read_text(encoding='utf-8'))
    if (not isinstance(config,dict) or set(config)!={'bot_token','owner_id','socket_path','lock_path'}
            or not isinstance(config['bot_token'],str) or not re.fullmatch(r'[0-9]{5,15}:[A-Za-z0-9_-]{20,100}',config['bot_token'])
            or type(config['owner_id']) is not int or config['owner_id']<=0
            or any(not isinstance(config[k],str) or not Path(config[k]).is_absolute() for k in ('socket_path','lock_path'))):
        raise ValueError('invalid configuration')
    return config


def incoming(update, owner):
    if not isinstance(update,dict): return None
    message=update.get('message')
    if not isinstance(message,dict) or any(k in message for k in ('forward_date','forward_from','forward_origin','forward_from_chat')): return None
    sender=message.get('from'); chat=message.get('chat'); mid=message.get('message_id'); text=message.get('text')
    if (type(owner) is not int or owner<=0 or not isinstance(sender,dict) or not isinstance(chat,dict)
            or type(sender.get('id')) is not int or type(chat.get('id')) is not int
            or sender.get('id')!=owner or chat.get('id')!=owner
            or sender.get('is_bot') is not False or chat.get('type')!='private' or type(mid) is not int or mid<=0
            or not isinstance(text,str) or len(text)>2200): return None
    if text in ('/mirror status','/mirror pause','/mirror resume'):
        text='/source_mirror '+text.split(' ')[1]
    elif not text.startswith('nailblog '): return None
    return mid,text


def api(token, method, payload):
    if method not in ('getUpdates','sendMessage'): raise ValueError('unsupported operation')
    request=Request('https://api.telegram.org/bot'+token+'/'+method,
                    json.dumps(payload,separators=(',',':')).encode(),{'Content-Type':'application/json'},method='POST')
    with build_opener(NoRedirect()).open(request,timeout=35) as response:
        raw=response.read(1_000_001)
    if len(raw)>1_000_000: raise ValueError('telegram response too large')
    value=json.loads(raw)
    if not isinstance(value,dict) or value.get('ok') is not True: raise ValueError('telegram request failed')
    return value.get('result')


def run(config):
    if fcntl is None or not hasattr(os,'O_NOFOLLOW'): raise ValueError('Linux process lock required')
    lock=Path(config['lock_path'])
    fd=os.open(lock,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        # A previous sender may have crashed after Telegram accepted a message.
        # Preserve uncertainty; never automatically repeat that notification.
        call(config['socket_path'],{'owner_id':config['owner_id'],'operation':'recover','message_id':None})
        offset=0
        while True:
            try:
                updates=api(config['bot_token'],'getUpdates',{'offset':offset,'timeout':25,'allowed_updates':['message']})
                if not isinstance(updates,list): raise ValueError('invalid update response')
                for update in updates:
                    if not isinstance(update,dict) or type(update.get('update_id')) is not int: continue
                    event=incoming(update,config['owner_id'])
                    if event is None:
                        offset=max(offset,update['update_id']+1)
                        continue
                    mid,text=event
                    try:
                        result=submit(config['socket_path'],config['owner_id'],mid,text)
                        # Re-delivery is safe for command state, but replies can repeat.
                        msg='Source Mirror: '+json.dumps(result,ensure_ascii=False,sort_keys=True)
                    except Exception:
                        # Do not advance the Telegram cursor before command persistence.
                        # A later retry reuses message identity and backend idempotence.
                        raise
                    api(config['bot_token'],'sendMessage',{'chat_id':config['owner_id'],'text':msg,'disable_web_page_preview':True})
                    offset=max(offset,update['update_id']+1)
                request={'owner_id':config['owner_id'],'operation':'claim','message_id':None}
                notice=call(config['socket_path'],request).get('notice')
                if notice is not None:
                    try:
                        api(config['bot_token'],'sendMessage',{'chat_id':config['owner_id'],
                            'text':'Source Mirror: '+json.dumps(notice['payload'],ensure_ascii=False,sort_keys=True),
                            'disable_web_page_preview':True})
                    except Exception:
                        call(config['socket_path'],{'owner_id':config['owner_id'],'operation':'unknown','message_id':notice['notice_id']})
                        raise
                    call(config['socket_path'],{'owner_id':config['owner_id'],'operation':'sent','message_id':notice['notice_id']})
            except Exception:
                time.sleep(5)
    finally: os.close(fd)


if __name__=='__main__':
    try: run(load_config('/etc/nailhub-source-mirror/telegram-bot.json'))
    except Exception: raise SystemExit('Source Mirror Telegram bot unavailable') from None
