"""Root-owned opt-in for future official publication; disabled if absent."""
from datetime import datetime,timezone
import os
from pathlib import Path
import stat
from .localization import strict_json
from .official_sources import SOURCES

PATH='/etc/nailhub-source-mirror/official-publish-policy.json'

def load():
    path=Path(PATH)
    if os.name!='posix': return {'enabled':False,'source_ids':[]}
    if not path.exists() and not path.is_symlink(): return {'enabled':False,'source_ids':[]}
    for component in (path,*path.parents):
        info=component.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid!=0 or info.st_mode & 0o022: raise ValueError('untrusted official publish policy')
    if not path.is_file() or path.stat().st_size>16_384: raise ValueError('invalid official publish policy')
    value=strict_json(path.read_text(encoding='utf-8'))
    if (not isinstance(value,dict) or set(value)!={'version','enabled','source_ids','valid_until_utc'}
            or value['version']!='official-publish-policy-v1' or type(value['enabled']) is not bool
            or not isinstance(value['source_ids'],list)
            or any(not isinstance(source,str) or source not in {item['id'] for item in SOURCES} for source in value['source_ids'])
            or len(set(value['source_ids']))!=len(value['source_ids'])):
        raise ValueError('invalid official publish policy')
    try: expiry=datetime.fromisoformat(value['valid_until_utc'].replace('Z','+00:00'))
    except (AttributeError,ValueError): raise ValueError('invalid official publish policy expiry') from None
    if expiry.tzinfo is None or expiry<=datetime.now(timezone.utc): raise ValueError('official publish policy expired')
    return value
