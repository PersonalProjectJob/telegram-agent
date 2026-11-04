"""Durable, bounded generation state; no model output or secrets stored."""
import os
from pathlib import Path
import re
import sqlite3

def _digest(value):return isinstance(value,str) and re.fullmatch(r'[0-9a-f]{64}',value) is not None

class GenerationState:
    def __init__(self,root):
        root=Path(root);path=root/'official-autostage.sqlite3'
        if not root.is_absolute() or root!=root.resolve() or root.is_symlink() or path.is_symlink():raise ValueError('unsafe generation state')
        self.db=sqlite3.connect(path,timeout=5,isolation_level=None)
        try:
            self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA synchronous=FULL')
            self.db.execute('''CREATE TABLE IF NOT EXISTS generation (
                source_id TEXT NOT NULL,source_sha TEXT NOT NULL,state TEXT NOT NULL,
                attempts INTEGER NOT NULL,draft_sha TEXT,reason TEXT,PRIMARY KEY(source_id,source_sha))''')
            self.db.execute('CREATE TABLE IF NOT EXISTS rotation (key TEXT PRIMARY KEY, position INTEGER NOT NULL)')
            self.db.execute('CREATE TABLE IF NOT EXISTS probe_heads (source_id TEXT PRIMARY KEY, source_sha TEXT NOT NULL)')
            if os.name=='posix':
                fd=os.open(root,os.O_RDONLY)
                try:os.fsync(fd)
                finally:os.close(fd)
        except BaseException:
            self.db.close();raise

    def close(self):self.db.close()

    def select_source(self,sources,published,restageable=None):
        """Round robin over fixed sources; HOLD and staged heads are skipped."""
        if (not isinstance(sources,list) or not sources or len(sources)!=len(set(sources))
                or any(not isinstance(s,str) or not s for s in sources) or not isinstance(published,set)):
            raise ValueError('invalid source rotation')
        if restageable is None:restageable={}
        if not isinstance(restageable,dict):raise ValueError('invalid restage selection')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row=self.db.execute("SELECT position FROM rotation WHERE key='next'").fetchone()
            start=(row[0] if row else 0)%len(sources);selected=None
            for offset in range(len(sources)):
                index=(start+offset)%len(sources);source=sources[index]
                if source in published:continue
                head=self.db.execute('SELECT source_sha,state FROM generation WHERE source_id=? ORDER BY rowid DESC LIMIT 1',(source,)).fetchone()
                probe=self.db.execute('SELECT source_sha FROM probe_heads WHERE source_id=?',(source,)).fetchone()
                if head and head[1] in ('held','generating'):
                    if not probe or probe[0]==head[0] or self.db.execute('SELECT 1 FROM generation WHERE source_id=? AND source_sha=?',(source,probe[0])).fetchone():continue
                if head and head[1]=='ready':
                    stage=self.db.execute('SELECT state FROM generation WHERE source_id=? AND source_sha=?',('stage:'+source,head[0])).fetchone()
                    if stage and stage[0] in ('held','generating','ready'):
                        if stage[0]=='ready' and restageable.get(source)==head[0]:
                            if self.db.execute("SELECT 1 FROM generation WHERE source_id LIKE ? AND source_sha=? AND state IN ('held','generating') LIMIT 1",('restage:'+source+':%',head[0])).fetchone():continue
                        elif stage[0]=='ready' or not probe or probe[0]==head[0] or self.db.execute('SELECT 1 FROM generation WHERE source_id=? AND source_sha=?',(source,probe[0])).fetchone():continue
                selected=source
                self.db.execute("INSERT INTO rotation VALUES ('next',?) ON CONFLICT(key) DO UPDATE SET position=excluded.position",((index+1)%len(sources),))
                break
            self.db.execute('COMMIT');return selected
        except BaseException:
            self.db.execute('ROLLBACK');raise

    def select_probe(self,sources,published):
        """Choose one held source to refetch only; never alter its HOLD."""
        if (not isinstance(sources,list) or not sources or len(sources)!=len(set(sources))
                or any(not isinstance(s,str) or not s for s in sources) or not isinstance(published,set)):
            raise ValueError('invalid source rotation')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row=self.db.execute("SELECT position FROM rotation WHERE key='probe'").fetchone()
            start=(row[0] if row else 0)%len(sources);selected=None
            for offset in range(len(sources)):
                index=(start+offset)%len(sources);source=sources[index]
                if source in published:continue
                head=self.db.execute('SELECT source_sha,state FROM generation WHERE source_id=? ORDER BY rowid DESC LIMIT 1',(source,)).fetchone()
                if not head:continue
                stage=self.db.execute('SELECT state FROM generation WHERE source_id=? AND source_sha=?',('stage:'+source,head[0])).fetchone()
                if head[1] not in ('held','generating') and (not stage or stage[0] not in ('held','generating')):continue
                selected=source
                self.db.execute("INSERT INTO rotation VALUES ('probe',?) ON CONFLICT(key) DO UPDATE SET position=excluded.position",((index+1)%len(sources),))
                break
            self.db.execute('COMMIT');return selected
        except BaseException:
            self.db.execute('ROLLBACK');raise

    def record_probe(self,source_id,source_sha):
        if not isinstance(source_id,str) or not source_id or not _digest(source_sha):raise ValueError('invalid probe identity')
        self.db.execute('INSERT INTO probe_heads VALUES (?,?) ON CONFLICT(source_id) DO UPDATE SET source_sha=excluded.source_sha',(source_id,source_sha))

    def clear_probe(self,source_id):
        self.db.execute('DELETE FROM probe_heads WHERE source_id=?',(source_id,))

    def held_snapshots(self,source_id):
        rows=self.db.execute("SELECT DISTINCT source_sha FROM generation WHERE source_id IN (?,?) AND state IN ('held','generating') LIMIT 101",(source_id,'stage:'+source_id)).fetchall()
        if len(rows)>100 or any(not _digest(row[0]) for row in rows):raise ValueError('held source history unavailable')
        return [row[0] for row in rows]

    def claim(self,source_id,source_sha):
        if not isinstance(source_id,str) or not source_id or not _digest(source_sha):raise ValueError('invalid generation identity')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row=self.db.execute('SELECT state,attempts,draft_sha FROM generation WHERE source_id=? AND source_sha=?',(source_id,source_sha)).fetchone()
            if row is None:
                self.db.execute("INSERT INTO generation VALUES (?,?,'generating',1,NULL,NULL)",(source_id,source_sha));result=('generate',None)
            elif row[0]=='ready' and _digest(row[2]):result=('ready',row[2])
            elif row[0]=='deferred':
                self.db.execute("UPDATE generation SET state='generating',attempts=attempts+1,reason=NULL WHERE source_id=? AND source_sha=?",(source_id,source_sha));result=('generate',None)
            elif row[0]=='generating':
                self.db.execute("UPDATE generation SET state='held',reason='interrupted' WHERE source_id=? AND source_sha=?",(source_id,source_sha));result=('held',None)
            else:result=('held',None)
            self.db.execute('COMMIT');return result
        except BaseException:
            self.db.execute('ROLLBACK');raise

    def defer(self,source_id,source_sha):
        # No external generation or upload happened after the stage claim.
        self.db.execute('BEGIN IMMEDIATE')
        try:
            changed=self.db.execute("UPDATE generation SET state='deferred',reason='window_elapsed' WHERE source_id=? AND source_sha=? AND state='generating'",(source_id,source_sha)).rowcount
            if changed!=1:raise ValueError('stage claim unavailable')
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK');raise

    def finish(self,source_id,source_sha,draft_sha=None):
        if draft_sha is not None and not _digest(draft_sha):
            raise ValueError('invalid generation outcome')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row=self.db.execute('SELECT state,attempts FROM generation WHERE source_id=? AND source_sha=?',(source_id,source_sha)).fetchone()
            if not row or row[0]!='generating':raise ValueError('generation claim unavailable')
            state='ready' if draft_sha is not None else 'held'
            reason=None if draft_sha is not None else 'quality_or_unavailable'
            self.db.execute('UPDATE generation SET state=?,draft_sha=?,reason=? WHERE source_id=? AND source_sha=?',(state,draft_sha,reason,source_id,source_sha))
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK');raise
