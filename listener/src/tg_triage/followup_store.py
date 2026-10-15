"""Additive durable conversation jobs; no mutation of original case/group accounting."""
import json
import threading
from datetime import timedelta


def _locked(method):
    def call(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return call


class FollowupStoreMixin:
    def init_followups(self, recover):
        self._followup_execution_lock = threading.RLock()
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS followup_roots (
                root_key TEXT PRIMARY KEY, chat_id INTEGER NOT NULL,
                source_message_id INTEGER NOT NULL, group_key TEXT, case_key TEXT NOT NULL,
                issue_number INTEGER, siblings TEXT NOT NULL DEFAULT '[]', split_plan TEXT);
            CREATE TABLE IF NOT EXISTS followup_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL, root_key TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued', plan TEXT, result TEXT,
                UNIQUE(chat_id,message_id));
            CREATE TABLE IF NOT EXISTS followup_operations (
                job_id INTEGER NOT NULL, ordinal INTEGER NOT NULL, kind TEXT NOT NULL,
                payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'planned',
                reserved_at TEXT, result INTEGER, PRIMARY KEY(job_id,ordinal));
        """)
        existing = {row[1] for row in self.connection.execute('PRAGMA table_info(followup_roots)')}
        if 'split_plan' not in existing:
            self.connection.execute('ALTER TABLE followup_roots ADD COLUMN split_plan TEXT')
        if recover:
            self.connection.execute("UPDATE followup_operations SET status='ambiguous' WHERE status='reserved'")
            self.connection.execute("""UPDATE followup_jobs SET status='ambiguous'
                WHERE id IN (SELECT job_id FROM followup_operations WHERE status='ambiguous')""")

    def followup_source(self, message):
        """Only same-chat sent notice records or their proven mapped source may resolve a root."""
        if message.reply_to_id is None:
            return None
        with self._lock:
            notice = self.connection.execute("""SELECT message_id FROM group_notices
                WHERE chat_id=? AND status='sent' AND (notice_message_id=? OR message_id=?)
                ORDER BY reserved_at DESC LIMIT 1""",
                (message.chat_id, message.reply_to_id, message.reply_to_id)).fetchone()
            if notice is None:
                return None
            job = self.connection.execute("SELECT root_key FROM followup_jobs WHERE chat_id=? AND message_id=?",
                                          (message.chat_id, notice[0])).fetchone()
            if job:
                return dict(self.connection.execute('SELECT * FROM followup_roots WHERE root_key=?',
                                                    (job[0],)).fetchone())
            source = self.get(message.chat_id, notice[0])
            if source is None or source['case_key'] is None:
                return None
            return dict(root_key=f"{message.chat_id}:{source['group_key'] or source['case_key']}",
                        chat_id=message.chat_id, source_message_id=notice[0], group_key=source['group_key'],
                        case_key=source['case_key'], issue_number=source['issue_number'], siblings='[]')

    def enqueue_followup(self, message, source):
        with self._lock, self.connection:
            self.connection.execute("""INSERT OR IGNORE INTO followup_roots
                (root_key,chat_id,source_message_id,group_key,case_key,issue_number)
                VALUES (:root_key,:chat_id,:source_message_id,:group_key,:case_key,:issue_number)""", source)
            self.connection.execute("""INSERT OR IGNORE INTO followup_jobs(chat_id,message_id,root_key)
                VALUES (?,?,?)""", (message.chat_id,message.message_id,source['root_key']))
            self.connection.execute("""UPDATE messages SET decision='followup',tier='none',case_key=?,group_key=?,
                issue_number=?,issue_url=?,processed_at=? WHERE chat_id=? AND message_id=?""",
                (source['case_key'],source['group_key'],source['issue_number'],
                 self.get(source['chat_id'],source['source_message_id'])['issue_url'],self.clock().isoformat(),
                 message.chat_id,message.message_id))

    def pending_followups(self):
        with self._lock:
            return [dict(row) for row in self.connection.execute("""SELECT * FROM followup_jobs
                WHERE status IN ('queued','planned','ambiguous') ORDER BY id""")]

    def followup_context(self, job):
        from .store import _to_message
        with self._lock:
            root = dict(self.connection.execute('SELECT * FROM followup_roots WHERE root_key=?',
                                               (job['root_key'],)).fetchone())
            column = 'group_key' if root['group_key'] else 'case_key'
            rows = self.connection.execute(f"""SELECT * FROM messages WHERE chat_id=? AND {column}=?
                AND decision != 'followup' ORDER BY message_id""",
                (root['chat_id'],root[column])).fetchall()
            reply = _to_message(self.get(job['chat_id'],job['message_id']))
            history = [dict(r) for r in self.connection.execute("""SELECT m.text,j.plan,j.result
                FROM followup_jobs j JOIN messages m ON m.chat_id=j.chat_id AND m.message_id=j.message_id
                WHERE j.root_key=? AND j.id<? ORDER BY j.id DESC LIMIT 5""", (job['root_key'],job['id']))]
            return root, [_to_message(row) for row in rows], reply, history

    def save_followup_plan(self, job, plan, operations):
        with self._lock, self.connection:
            changed = self.connection.execute("UPDATE followup_jobs SET plan=?,status='planned' WHERE id=? AND status='queued'",
                                              (json.dumps(plan,ensure_ascii=False),job['id']))
            if changed.rowcount != 1:
                raise RuntimeError('Followup already planned')
            if plan.get('action')=='split':
                self.connection.execute('UPDATE followup_roots SET split_plan=? WHERE root_key=?',
                                        (json.dumps(plan,ensure_ascii=False),job['root_key']))
            for ordinal,(kind,payload) in enumerate(operations):
                self.connection.execute("""INSERT INTO followup_operations(job_id,ordinal,kind,payload)
                    VALUES (?,?,?,?)""", (job['id'],ordinal,kind,json.dumps(payload,ensure_ascii=False)))

    @_locked
    def followup_operations(self, job):
        return [dict(row) for row in self.connection.execute(
            'SELECT * FROM followup_operations WHERE job_id=? ORDER BY ordinal', (job['id'],))]

    def reserve_followup_operation(self, job, ordinal, limit):
        with self._lock:
            try:
                self.connection.execute('BEGIN IMMEDIATE')
                row=self.connection.execute('SELECT * FROM followup_operations WHERE job_id=? AND ordinal=?',
                                            (job['id'],ordinal)).fetchone()
                if row['status'] != 'planned':
                    self.connection.rollback(); return False
                if row['kind']=='create' and self.count_recent_issues() >= limit:
                    self.connection.rollback(); return False
                self.connection.execute("UPDATE followup_operations SET status='reserved',reserved_at=? WHERE job_id=? AND ordinal=?",
                                        (self.clock().isoformat(),job['id'],ordinal))
                self.connection.commit()
                return True
            except Exception:
                self.connection.rollback()
                raise

    def finish_followup_operation(self, job, op, number=None):
        with self._lock, self.connection:
            changed=self.connection.execute("""UPDATE followup_operations SET status='done',result=?
                WHERE job_id=? AND ordinal=? AND status='reserved'""", (number,job['id'],op['ordinal']))
            if changed.rowcount != 1:
                return
            if op['kind']=='create':
                root=self.connection.execute('SELECT siblings FROM followup_roots WHERE root_key=?', (job['root_key'],)).fetchone()
                siblings=json.loads(root[0]); siblings.append(number)
                self.connection.execute('UPDATE followup_roots SET siblings=? WHERE root_key=?',
                                        (json.dumps(siblings),job['root_key']))

    def remember_followup_result(self, job, op, number):
        """Best-effort DB recovery for a proven response; never repeat the external POST."""
        with self._lock, self.connection:
            row=self.connection.execute('SELECT result FROM followup_operations WHERE job_id=? AND ordinal=?',
                                        (job['id'],op['ordinal'])).fetchone()
            if row is None or row[0] is not None:
                return
            self.connection.execute('UPDATE followup_operations SET result=? WHERE job_id=? AND ordinal=?',
                                    (number,job['id'],op['ordinal']))
            if op['kind']=='create':
                root=self.connection.execute('SELECT siblings FROM followup_roots WHERE root_key=?',
                                             (job['root_key'],)).fetchone()
                siblings=json.loads(root[0])
                if number not in siblings:
                    siblings.append(number)
                    self.connection.execute('UPDATE followup_roots SET siblings=? WHERE root_key=?',
                                            (json.dumps(siblings),job['root_key']))

    def finish_followup(self, job, status, result):
        with self._lock, self.connection:
            self.connection.execute('UPDATE followup_jobs SET status=?,result=? WHERE id=?', (status,result,job['id']))

    @_locked
    def followup_split_attempted(self, root_key, before_id):
        rows=self.connection.execute('SELECT plan FROM followup_jobs WHERE root_key=? AND id<? AND plan IS NOT NULL AND status != "dryrun"',
                                     (root_key,before_id)).fetchall()
        return any(json.loads(row[0]).get('action')=='split' for row in rows)

    @_locked
    def followup_create_count(self, now):
        return self.connection.execute("""SELECT COUNT(*) FROM followup_operations WHERE kind='create'
            AND reserved_at IS NOT NULL AND julianday(reserved_at)>=julianday(?)""",
            ((now-timedelta(minutes=60)).isoformat(),)).fetchone()[0]

    @_locked
    def followup_media(self, root, messages, repo):
        """Reuse only proven original uploads; never reupload media while correcting a ticket."""
        import re
        from .attachments import release_tag
        images,documents=[],[]
        for item in messages:
            rows=self.connection.execute("""SELECT asset_name FROM attachments
                WHERE case_key=? AND message_id=? AND status='uploaded'""",
                (root['group_key'] or root['case_key'],item.message_id)).fetchall()
            for row in rows:
                asset=row[0]
                if not re.fullmatch(r'[0-9]+-[0-9]+\.[a-z0-9]+',asset):
                    continue
                url=f"https://github.com/{repo}/releases/download/{release_tag(root['group_key'] or root['case_key'])}/{asset}"
                if item.has_image:
                    images.append(('Ảnh',url))
                elif item.has_document:
                    documents.append((item.document_name,url))
        return images,documents

    def ensure_followup_job(self, chat_id, message_id):
        """Explicit one-message legacy replay only, never a global backfill or case rearm."""
        from .store import _to_message
        with self._lock:
            row=self.get(chat_id,message_id)
            if row is None or row['decision']!='followup':
                return False
            existing=self.connection.execute('SELECT 1 FROM followup_jobs WHERE chat_id=? AND message_id=?',
                                             (chat_id,message_id)).fetchone()
            if existing:
                return False
            message=_to_message(row)
            source=self.followup_source(message)
            if source is None:
                return False
            self.enqueue_followup(message,source)
            return True
