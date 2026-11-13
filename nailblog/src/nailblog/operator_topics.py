"""Explicit operator-selected production topics, recorded independently of daily decisions."""
import json
import os
from datetime import datetime

from .config import CATEGORIES, PRODUCTION_MEDIA_ORIGIN
from .publication import binding, digest
from .schedule import VN_TZ, image_deadline, iso_slot
from .state import StateError


def _instant(value):
    try:
        value = datetime.fromisoformat(value)
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError()
        return value.astimezone(VN_TZ)
    except (TypeError, ValueError):
        raise StateError('operator_slot_invalid') from None


def _fingerprint(config, topic, approval_ref):
    return digest({'environment': binding(config), 'slot': topic['slot_at'], 'title': topic['title'],
                   'category': topic['category'], 'sources_json': topic['sources_json'], 'approval_ref': approval_ref})


def select(service, *, slot, title, category, approval_ref, now):
    """Create exactly one selection, or return the same selection on replay; never arm or publish."""
    config, state = service.config, service.state
    if (config.mode != 'live' or config.contract.environment != 'production' or
            config.contract.verified is not True or config.contract.media_origin != PRODUCTION_MEDIA_ORIGIN or
            config.autoflow.owner_approval is not True or not config.autoflow.enabled):
        raise StateError('operator_production_manual_mode_required')
    if not state.control()['paused']:
        raise StateError('operator_selection_requires_pause')
    local = _instant(slot)
    slot_at = iso_slot(local)
    if local.strftime('%H:%M') not in config.slots or local.second or local.microsecond:
        raise StateError('operator_slot_or_deadline_invalid')
    if (category not in CATEGORIES or category not in config.daily.slot_categories.get(local.strftime('%H:%M'), ()) or
            not isinstance(title, str) or not 5 <= len(title.strip()) <= 200 or
            not title.isprintable() or not isinstance(approval_ref, str) or not approval_ref.isprintable() or
            not 1 <= len(approval_ref.strip()) <= 200):
        raise StateError('operator_selection_invalid')
    title, approval_ref = title.strip(), approval_ref.strip()
    sources = json.dumps({'sources': [], 'product_promo': False}, ensure_ascii=False)
    selected = {'slot_at': slot_at, 'title': title, 'category': category, 'sources_json': sources}
    fingerprint = _fingerprint(config, selected, approval_ref)
    with state.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        if not db.execute('SELECT paused FROM control WHERE id=1').fetchone()[0]:
            raise StateError('operator_selection_requires_pause')
        has_table = db.execute("SELECT 1 FROM sqlite_master WHERE name='operator_topic_selections'").fetchone()
        existing = db.execute('SELECT * FROM operator_topic_selections WHERE slot_at=?', (slot_at,)).fetchone() if has_table else None
        if existing:
            topic = db.execute('SELECT * FROM topics WHERE id=?', (existing['topic_id'],)).fetchone()
            if (topic and topic['state'] == 'approved' and existing['fingerprint'] == fingerprint and
                    _fingerprint(config, topic, existing['approval_ref']) == fingerprint):
                return {'topic_id': existing['topic_id'], 'slot_at': slot_at, 'created': False}
            raise StateError('operator_selection_conflict')
        if now >= image_deadline(local, config.schedule.window_end_hours).timestamp():
            raise StateError('operator_slot_or_deadline_invalid')
        db.execute('''CREATE TABLE IF NOT EXISTS operator_topic_selections(
            slot_at TEXT PRIMARY KEY, topic_id INTEGER NOT NULL UNIQUE, fingerprint TEXT NOT NULL,
            approval_ref TEXT NOT NULL, actor_uid INTEGER NOT NULL, selected_at REAL NOT NULL)''')
        for row in db.execute("SELECT slot_at FROM topics WHERE state='approved' AND slot_at IS NOT NULL"):
            if _instant(row['slot_at']) == local:
                raise StateError('operator_slot_taken')
        for row in db.execute("SELECT slot_at FROM posts WHERE state NOT IN ('held') AND slot_at IS NOT NULL"):
            if _instant(row['slot_at']) == local:
                raise StateError('operator_slot_taken')
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='autoflow_runs'").fetchone():
            for row in db.execute('SELECT slot_at FROM autoflow_runs'):
                if _instant(row['slot_at']) == local:
                    raise StateError('operator_slot_taken')
        topic_id = db.execute('INSERT INTO topics(week,title,category,slot_at,score,sources_json,state) '
                              "VALUES(?,?,?,?,0,?,'approved')",
                              ('operator:' + local.date().isoformat(), title, category, slot_at, sources)).lastrowid
        db.execute('INSERT INTO operator_topic_selections VALUES(?,?,?,?,?,?)',
                   (slot_at, topic_id, fingerprint, approval_ref, os.getuid() if hasattr(os, 'getuid') else -1, now))
        return {'topic_id': topic_id, 'slot_at': slot_at, 'created': True}


def locked(state, config, topic):
    """Return None for non-operator topics, otherwise verify the exact Owner selection."""
    operator_topic = str(topic.get('week', '')).startswith('operator:')
    if config is None or config.contract.environment != 'production' or topic.get('state') != 'approved':
        return False if operator_topic else None
    with state.connection() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='operator_topic_selections'").fetchone():
            return False if operator_topic else None
        row = db.execute('SELECT * FROM operator_topic_selections WHERE topic_id=?', (topic['id'],)).fetchone()
    if row is None:
        return False if operator_topic else None
    return bool(row and row['slot_at'] == topic['slot_at'] and
                row['fingerprint'] == _fingerprint(config, topic, row['approval_ref']))
