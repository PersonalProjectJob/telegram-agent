"""Durable admin operations and approval binding, independent of transient post details."""
import json
from datetime import datetime, timedelta

from .override_policy import NEVER_OVERRIDABLE
from .publication import (AdminError, approval_fingerprint, binding, digest, document_matches,
                          instant, matches, snapshot)
from .schedule import VN_TZ
from .admin_images import verify_reviews, verify_test_operation
from .staging_immediate import (bound_approval_audit, immediate_approval,
                                immediate_publish_allowed)


class AdminStore:
    def __init__(self, state, config):
        self.state, self.config = state, config
        self.env = binding(config)
        with state.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS admin_binding(id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS admin_operations(post_id INTEGER PRIMARY KEY,
                    intent TEXT NOT NULL, payload TEXT NOT NULL, receipt TEXT NOT NULL DEFAULT '{}',
                    approval TEXT NOT NULL DEFAULT '', publish_day TEXT NOT NULL DEFAULT '');
            ''')
            db.execute('BEGIN IMMEDIATE')
            value = json.dumps(self.env, sort_keys=True)
            db.execute('INSERT OR IGNORE INTO admin_binding VALUES(1,?)', (value,))
            if db.execute('SELECT value FROM admin_binding WHERE id=1').fetchone()[0] != value:
                raise AdminError('state_environment_mismatch')

    def operation(self, post_id):
        with self.state.connection() as db:
            row = db.execute('SELECT * FROM admin_operations WHERE post_id=?', (post_id,)).fetchone()
            return dict(row) if row else None

    def claim_create(self, post_id, payload):
        snapshot(payload, self.config)
        with self.state.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM posts WHERE id=?', (post_id,)).fetchone()
            if not row or row['state'] not in ('writing', 'review') or row['admin_id']:
                raise AdminError('draft_state_invalid')
            document=json.loads(row['draft_json'])
            if document.get('test_image_waiver'):
                verify_test_operation(document,self.config,slot_at=row['slot_at'],state=self.state,post_id=post_id)
                verify_reviews(document,document.get('image_hashes',[]),config=self.config)
            if db.execute('SELECT paused FROM control WHERE id=1').fetchone()[0]:
                raise AdminError('paused')
            if instant(payload['publishedAt']) != instant(row['slot_at']):
                raise AdminError('slot_payload_mismatch')
            document_matches(row, payload, self.config)
            if db.execute('SELECT 1 FROM admin_operations WHERE post_id=?', (post_id,)).fetchone():
                raise AdminError('create_requires_reconciliation')
            intent = digest({'post': dict(row), 'environment': self.env, 'payload': snapshot(payload, self.config)})
            db.execute('INSERT INTO admin_operations(post_id,intent,payload) VALUES(?,?,?)',
                       (post_id, intent, json.dumps(payload, ensure_ascii=False)))

    def check_write(self, post_id):
        row = self.state.get_post(post_id)
        if self.state.control()['paused'] or not row or row['state'] not in ('writing', 'review') or row['admin_id']:
            raise AdminError('draft_write_no_longer_allowed')
        document=json.loads(row['draft_json'])
        if document.get('test_image_waiver'):
            verify_test_operation(document,self.config,slot_at=row['slot_at'],state=self.state,post_id=post_id)
            verify_reviews(document,document.get('image_hashes',[]),config=self.config)

    def materialized(self, post_id, payload):
        """Only substitute uploaded image URLs; no model/editor content can change here."""
        with self.state.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM posts WHERE id=?', (post_id,)).fetchone()
            op = db.execute('SELECT * FROM admin_operations WHERE post_id=?', (post_id,)).fetchone()
            if (not row or not op or row['state'] not in ('writing','review') or row['admin_id'] or
                    db.execute('SELECT paused FROM control WHERE id=1').fetchone()[0]):
                raise AdminError('draft_write_no_longer_allowed')
            old, new = json.loads(op['payload']), json.loads(json.dumps(payload))
            original = document_matches(row, old, self.config)
            if len(old['images']) != len(new['images']):
                raise AdminError('materialized_payload_changed')
            # Restore new URLs to old ones before comparing all article fields.
            restored = json.loads(json.dumps(new))
            content = restored['translations'][0]['content']
            for a, b in zip(old['images'], new['images']):
                content = content.replace(b['imageUrl'], a['imageUrl'])
            restored['translations'][0]['content'] = content
            restored['images'], restored['coverImage'] = old['images'], old['coverImage']
            if snapshot(restored, self.config) != snapshot(old, self.config):
                raise AdminError('materialized_payload_changed')
            original['content_html'] = new['translations'][0]['content']
            db.execute('UPDATE posts SET draft_json=? WHERE id=?', (json.dumps(original, ensure_ascii=False), post_id))
            db.execute('UPDATE admin_operations SET payload=? WHERE post_id=?', (json.dumps(new, ensure_ascii=False), post_id))

    def record_draft(self, post_id, actual):
        with self.state.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            op = db.execute('SELECT * FROM admin_operations WHERE post_id=?', (post_id,)).fetchone()
            row = db.execute('SELECT * FROM posts WHERE id=?', (post_id,)).fetchone()
            if (not op or not row or row['state'] not in ('writing', 'review') or
                    not matches(actual, json.loads(op['payload']), self.config) or not actual.get('id') or
                    (row['admin_id'] and row['admin_id'] != actual['id'])):
                raise AdminError('draft_readback_mismatch')
            db.execute('UPDATE admin_operations SET receipt=?,approval=? WHERE post_id=?',
                       (json.dumps(actual, ensure_ascii=False), '', post_id))
            db.execute('UPDATE posts SET admin_id=? WHERE id=?', (actual['id'], post_id))
            if row['state'] == 'writing':
                self.state.transition(post_id, 'review', 'draft_verified', _db=db)

    @staticmethod
    def _override_details(db, document, override):
        """Evidence for a Draft that failed the gate but carries the Owner's override, else AdminError.

        Needs, for BOTH kinds (images, draft): an `approved` row of exactly this run and snapshot with override=1,
        a non-empty reason, every stored issue overridable and outside NEVER_OVERRIDABLE, and the current quality
        issues all covered by those stored issues (nothing new). Any miss keeps the old refusal."""
        refuse = AdminError('quality_pass_required')
        if not isinstance(override, dict):
            raise refuse
        approvals, run_id = override.get('approvals'), override.get('run_id')
        snapshots = {'images': override.get('images_snapshot'), 'draft': override.get('draft_snapshot')}
        quality = document.get('quality') or {}
        current = quality.get('issues')
        if (approvals is None or not isinstance(run_id, str) or not run_id or quality.get('verdict') != 'fixable'
                or not isinstance(current, list) or not current):
            raise refuse
        for issue in current:   # a hold, or a never-overridable code, can never ride an override
            if not isinstance(issue, dict) or issue.get('kind') == 'hold' or issue.get('code') in NEVER_OVERRIDABLE:
                raise refuse
        by, reasons = None, {}
        for kind, snapshot in snapshots.items():
            if not approvals.is_approved(run_id, kind, snapshot):
                raise refuse
            row = db.execute("SELECT * FROM approval_requests WHERE run_id=? AND kind=? AND snapshot=? "
                             "AND status='approved'", (run_id, kind, snapshot)).fetchone()
            reason = ' '.join(str(row['reason'] or '').split()) if row else ''
            if not row or row['override'] != 1 or not reason:
                raise refuse
            try:
                stored = json.loads(row['issues'] or '[]')
            except ValueError:
                raise refuse from None
            if (not isinstance(stored, list) or not stored or
                    any(not isinstance(i, dict) or i.get('overridable') is not True or i.get('kind') == 'hold' or
                        i.get('code') in NEVER_OVERRIDABLE for i in stored)):
                raise refuse
            if {i.get('code') for i in current} - {i.get('code') for i in stored}:
                raise refuse
            by, reasons[kind] = row['decided_by'], reason
        return {'override': {'reason': reasons['draft'], 'by': by, 'run_id': run_id, 'reasons': reasons,
                             'issues': sorted({i['code'] for i in current}), 'snapshots': snapshots}}

    def approve(self, post_id, now, *, override=None):
        """`override` (autoflow only): {'approvals': ApprovalStore, 'run_id', 'images_snapshot', 'draft_snapshot'}.
        Without it a verdict other than `pass` is refused exactly as before."""
        with self.state.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM posts WHERE id=?', (post_id,)).fetchone()
            op = db.execute('SELECT * FROM admin_operations WHERE post_id=?', (post_id,)).fetchone()
            if not row or not op or row['state'] != 'review' or not row['admin_id']:
                raise AdminError('verified_draft_required')
            document = json.loads(row['draft_json'])
            verify_test_operation(document,self.config,slot_at=row['slot_at'],state=self.state,post_id=post_id)
            document_matches(row, json.loads(op['payload']), self.config)
            verify_reviews(document, document.get('image_hashes', []),config=self.config)
            if not 4 <= len(document.get('image_hashes', [])) <= 6:
                raise AdminError('image_review_required')
            details = None
            if document.get('quality', {}).get('verdict') != 'pass':
                details = self._override_details(db, document, override)   # verdict is never rewritten to pass
            immediate = immediate_approval(self.config, self.state, row, op, now, db)
            if immediate is None and datetime.fromtimestamp(now, VN_TZ) > instant(row['slot_at']) - timedelta(hours=self.config.schedule.approve_deadline_hours):
                raise AdminError('approval_deadline_passed')
            if not matches(json.loads(op['receipt']), json.loads(op['payload']), self.config, post_id=row['admin_id']):
                raise AdminError('verified_draft_required')
            if op['approval']:
                raise AdminError('approval_already_recorded')
            db.execute('UPDATE admin_operations SET approval=? WHERE post_id=?',
                       (immediate or approval_fingerprint(row, op, self.config), post_id))
            self.state.transition(post_id, 'scheduled', 'owner_approved', details, _db=db)

    def claim_publish(self, post_id, now):
        with self.state.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM posts WHERE id=?', (post_id,)).fetchone()
            op = db.execute('SELECT * FROM admin_operations WHERE post_id=?', (post_id,)).fetchone()
            if not row or not op or row['state'] != 'scheduled':
                raise AdminError('publish_not_scheduled')
            if db.execute('SELECT paused FROM control WHERE id=1').fetchone()[0]:
                raise AdminError('paused')
            if not op['approval']:
                raise AdminError('approval_changed')
            bound_approval_audit(self.config, self.state, row, op)
            document_matches(row, json.loads(op['payload']), self.config)
            document=json.loads(row['draft_json'])
            if document.get('test_image_waiver'):
                verify_test_operation(document,self.config,slot_at=row['slot_at'],state=self.state,post_id=post_id)
                verify_reviews(document,document.get('image_hashes',[]),config=self.config)
            delta = now - instant(row['slot_at']).timestamp()
            immediate = immediate_publish_allowed(self.config, self.state, row, op, now, db)
            if not immediate and not 0 <= delta <= min(15, self.config.late_window_minutes) * 60:
                raise AdminError('outside_publish_window')
            day = datetime.fromtimestamp(now, VN_TZ).date().isoformat()
            if db.execute("SELECT COUNT(*) FROM admin_operations WHERE publish_day=?", (day,)).fetchone()[0] >= self.config.admin.daily_publish_limit:
                raise AdminError('daily_publish_limit')
            self.state.transition(post_id, 'before_publish', 'publish_intent', _db=db)
            db.execute('UPDATE admin_operations SET publish_day=? WHERE post_id=?', (day, post_id))
            return json.loads(op['payload'])

    def check_publish_write(self, post_id, now):
        row = self.state.get_post(post_id)
        op = self.operation(post_id)
        if (not row or not op or row['state'] != 'before_publish' or self.state.control()['paused'] or
                not op['approval']):
            raise AdminError('publish_write_no_longer_allowed')
        bound_approval_audit(self.config, self.state, row, op)
        with self.state.connection() as db:
            immediate = immediate_publish_allowed(self.config, self.state, row, op, now, db)
        if not immediate and not 0 <= now - instant(row['slot_at']).timestamp() <= min(15,self.config.late_window_minutes)*60:
            raise AdminError('publish_write_no_longer_allowed')
        document=json.loads(row['draft_json'])
        if document.get('test_image_waiver'):
            verify_test_operation(document,self.config,slot_at=row['slot_at'],state=self.state,post_id=post_id)
            verify_reviews(document,document.get('image_hashes',[]),config=self.config)

    def recovery_payload(self, post_id):
        row, op = self.state.get_post(post_id), self.operation(post_id)
        if not row or not op or not op['approval']:
            raise AdminError('approval_changed')
        bound_approval_audit(self.config, self.state, row, op)
        payload = json.loads(op['payload'])
        document_matches(row, payload, self.config)
        return payload
