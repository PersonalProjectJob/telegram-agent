"""Account-pinned Free-plan quota estimates, UTC days and durable uncertain reservations."""
import json
import math
import os
import re
import time
import hashlib
from datetime import datetime,timezone
from pathlib import Path

from ..paid import IntentExists
from ..state import State
from . import ImageError

_RECON_ROOT='/var/lib/agentfleet/nailblog-oneflow-deadline-20261020'
_RECON_RUN='thinkpad-staging-photo-deadline-20261020-1745-images'
_RECON_BINDING='13eb05168c174954066b8ca2f31d443cf6546f1a9d3acd3576cf401e6167889c'
_RECON_INTENT='673b05f01d261f69a8726694c5ff4c5a1f230dc4f11851c7d35eece1f629a29f'
_SECOND_INTENT='ff6bb690fd4800bf1cdd69b5c72170a0dbf81ce026851a29754ffd3059f3ccb4'
_SECOND_QUOTA='/var/lib/agentfleet/nailblog-cloudflare-quota/neurons.sqlite3'
_SECOND_MANIFEST_SHA='b99d76021c7b9659f3e3db6dacaa682e308e939f4a1e1135c5d087ebdcf1be2e'
_SECOND_EVIDENCE_SHA='653ad0bf5fb2652181a5f17ec9cfd222b3a5d0d3dc38b311a7f3626afdc58dce'


def amount(value):
    if type(value) not in (int,float) or not math.isfinite(value) or value < 0:
        raise ImageError('invalid_neuron_amount')
    return math.ceil(round(value*1000,8))


def utc_day(now):
    return datetime.fromtimestamp(now,timezone.utc).date().isoformat()


class NeuronLedger:
    def __init__(self, config, clock=time.time):
        self.config,self.clock = config,clock
        cfg=config.cloudflare
        if cfg.two_article_free_day and (cfg.review_input_allowance != 32768 or config.image.review_output_ceiling != 1024):
            raise ImageError('two_article_review_bounds_must_remain_32768_1024')
        if (not re.fullmatch(r'[0-9a-f]{32}',cfg.account_id) or not cfg.quota_db_path or
                not Path(cfg.quota_db_path).is_absolute() or not 0 < cfg.daily_neurons <= 10000):
            raise ImageError('cloudflare_account_quota_required')
        self.state=State(cfg.quota_db_path)
        with self.state.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS cf_identity(id INTEGER PRIMARY KEY CHECK(id=1),account TEXT NOT NULL,ceiling INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS cf_baselines(day TEXT PRIMARY KEY,amount INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS cf_runs(id TEXT PRIMARY KEY,binding TEXT NOT NULL,ceiling INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS cf_revisions(parent_id TEXT PRIMARY KEY,child_id TEXT NOT NULL UNIQUE,
                    day TEXT NOT NULL,parent_binding TEXT NOT NULL,child_binding TEXT NOT NULL,at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS cf_intents(id TEXT PRIMARY KEY,run_id TEXT NOT NULL,model TEXT NOT NULL,
                    day TEXT NOT NULL,reserved INTEGER NOT NULL,charged INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,metadata TEXT NOT NULL DEFAULT '{}');
                CREATE TABLE IF NOT EXISTS cf_day_slots(day TEXT NOT NULL,slot INTEGER NOT NULL,
                    run_id TEXT UNIQUE,binding TEXT NOT NULL DEFAULT '',ceiling INTEGER NOT NULL,
                    PRIMARY KEY(day,slot));
                CREATE TABLE IF NOT EXISTS cf_review_holds(intent TEXT PRIMARY KEY,run_id TEXT NOT NULL,
                    day TEXT NOT NULL,reserved INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS cf_review_links(intent TEXT PRIMARY KEY,generation TEXT NOT NULL,
                    run_id TEXT NOT NULL,day TEXT NOT NULL,reserved INTEGER NOT NULL);
            ''')
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT OR IGNORE INTO cf_identity VALUES(1,?,?)',(cfg.account_id,amount(cfg.daily_neurons)))
            identity=db.execute('SELECT account,ceiling FROM cf_identity').fetchone()
            if tuple(identity)!=(cfg.account_id,amount(cfg.daily_neurons)):
                raise ImageError('cloudflare_account_or_daily_cap_changed')

    def attest(self):
        cfg=self.config.cloudflare;now=self.clock()
        try:
            path=Path(cfg.free_proof_file)
            if not path.is_absolute() or path.is_symlink() or not 0 < path.stat().st_size <= 8192:
                raise ValueError()
            if os.name=='posix' and (path.stat().st_mode&0o077 or path.stat().st_uid!=os.getuid()):
                raise ValueError()
            proof=json.loads(path.read_text(encoding='utf-8'))
            observed,expires=proof['observed_at'],proof['expires_at']
            if (type(observed) not in (int,float) or type(expires) not in (int,float) or
                    not math.isfinite(observed) or not math.isfinite(expires) or
                    not now-3600 <= observed <= now+30 or not now < expires <= observed+3600 or
                    proof['account_id']!=cfg.account_id or proof['plan']!='Workers Free' or
                    proof['exclusive_use'] is not True or proof['utc_day']!=utc_day(now) or
                    Path(proof['quota_db_path']).resolve()!=Path(cfg.quota_db_path).resolve()):
                raise ValueError()
            baseline=amount(proof['baseline_neurons'])
            if baseline>amount(cfg.daily_neurons):raise ValueError()
        except (OSError,ValueError,TypeError,KeyError):
            raise ImageError('fresh_account_bound_free_proof_required') from None
        with self.state.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT OR IGNORE INTO cf_baselines VALUES(?,?)',(proof['utc_day'],baseline))
            if db.execute('SELECT amount FROM cf_baselines WHERE day=?',(proof['utc_day'],)).fetchone()[0]!=baseline:
                raise ImageError('cloudflare_baseline_changed')
        return proof

    @staticmethod
    def family(db,run_id):
        row=db.execute('SELECT parent_id FROM cf_revisions WHERE child_id=?',(run_id,)).fetchone()
        root=row[0] if row else run_id
        child=db.execute('SELECT child_id FROM cf_revisions WHERE parent_id=?',(root,)).fetchone()
        return root,([root,child[0]] if child else [root])

    @classmethod
    def slot(cls,db,run_id):
        root,_=cls.family(db,run_id)
        return db.execute('SELECT day,binding,ceiling FROM cf_day_slots WHERE run_id=?',(root,)).fetchone()

    def run(self,run_id,binding,ceiling,*,revision_of=None,parent_binding=None):
        if not isinstance(run_id,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',run_id) or not 0<ceiling<=9000:
            raise ImageError('run_neurons_must_be_within_9000')
        if self.config.cloudflare.two_article_free_day:
            if ceiling != 4500 or self.config.cloudflare.daily_neurons != 10000:
                raise ImageError('two_article_policy_requires_4500_run_10000_day')
            self.attest()
        with self.state.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM cf_revisions WHERE parent_id=?',(run_id,)).fetchone():
                raise ImageError('superseded_parent_dispatch_forbidden')
            if revision_of is not None:
                if (not self.config.cloudflare.two_article_free_day or revision_of==run_id or
                        not isinstance(parent_binding,str) or not parent_binding):
                    raise ImageError('revision_parent_invalid')
                parent=db.execute('SELECT binding,ceiling FROM cf_runs WHERE id=?',(revision_of,)).fetchone()
                slot=db.execute('SELECT day,binding,ceiling FROM cf_day_slots WHERE run_id=?',(revision_of,)).fetchone()
                old=db.execute('SELECT child_id,day,parent_binding,child_binding FROM cf_revisions WHERE parent_id=?',(revision_of,)).fetchone()
                if (not parent or not slot or tuple(parent)!=(parent_binding,amount(4500)) or
                        tuple(slot)!=(utc_day(self.clock()),parent_binding,amount(4500)) or
                        db.execute('SELECT 1 FROM cf_revisions WHERE child_id=?',(revision_of,)).fetchone()):
                    raise ImageError('revision_parent_slot_or_binding_invalid')
                expected=(run_id,slot[0],parent_binding,binding)
                if old and tuple(old)!=expected:raise ImageError('revision_branch_forbidden')
                if not old:
                    if db.execute('SELECT 1 FROM cf_runs WHERE id=?',(run_id,)).fetchone():
                        raise ImageError('revision_child_already_exists')
                    if (db.execute("SELECT 1 FROM cf_intents WHERE run_id=? AND status IN ('pending','uncertain')",(revision_of,)).fetchone() or
                            db.execute('SELECT 1 FROM cf_review_holds WHERE run_id=?',(revision_of,)).fetchone()):
                        raise ImageError('revision_parent_requires_reconciliation')
                    db.execute('INSERT INTO cf_runs VALUES(?,?,?)',(run_id,binding,amount(ceiling)))
                    db.execute('INSERT INTO cf_revisions VALUES(?,?,?,?,?,?)',(revision_of,run_id,slot[0],parent_binding,binding,self.clock()))
                row=db.execute('SELECT binding,ceiling FROM cf_runs WHERE id=?',(run_id,)).fetchone()
                if not row or tuple(row)!=(binding,amount(ceiling)):
                    raise ImageError('cloudflare_run_binding_or_ceiling_changed')
                return
            if self.config.cloudflare.two_article_free_day:
                day=utc_day(self.clock())
                revision=db.execute('SELECT child_binding,day FROM cf_revisions WHERE child_id=?',(run_id,)).fetchone()
                if revision:
                    if tuple(revision)!=(binding,day):raise ImageError('article_slot_day_or_binding_changed')
                    self.verify_existing(db,run_id,binding,ceiling)
                    return
                owned=db.execute('SELECT day,binding FROM cf_day_slots WHERE run_id=?',(run_id,)).fetchone()
                if owned and tuple(owned)!=(day,binding):
                    raise ImageError('article_slot_day_or_binding_changed')
                if not owned:
                    for slot in (1,2):
                        db.execute('INSERT OR IGNORE INTO cf_day_slots(day,slot,ceiling) VALUES(?,?,4500000)',(day,slot))
                    if self.other_usage(db,day)>1000000:
                        raise ImageError('two_article_other_use_allowance_exceeded')
                    available=db.execute('SELECT slot FROM cf_day_slots WHERE day=? AND run_id IS NULL ORDER BY slot LIMIT 1',(day,)).fetchone()
                    if not available:raise ImageError('two_article_slots_full')
                    db.execute('UPDATE cf_day_slots SET run_id=?,binding=? WHERE day=? AND slot=?',(run_id,binding,day,available[0]))
            db.execute('INSERT OR IGNORE INTO cf_runs VALUES(?,?,?)',(run_id,binding,amount(ceiling)))
            if tuple(db.execute('SELECT binding,ceiling FROM cf_runs WHERE id=?',(run_id,)).fetchone())!=(binding,amount(ceiling)):
                raise ImageError('cloudflare_run_binding_or_ceiling_changed')

    @staticmethod
    def verify_existing(db,run_id,binding,ceiling):
        row=db.execute('SELECT binding,ceiling FROM cf_runs WHERE id=?',(run_id,)).fetchone()
        if not row or tuple(row)!=(binding,amount(ceiling)):
            raise ImageError('cloudflare_run_binding_or_ceiling_changed')

    def verify_run(self,run_id,binding,ceiling):
        """Completed local cache inspection never renews proof or claims quota."""
        with self.state.connection() as db:
            row=db.execute('SELECT binding,ceiling FROM cf_runs WHERE id=?',(run_id,)).fetchone()
            if not row or tuple(row)!=(binding,amount(ceiling)):
                raise ImageError('cloudflare_run_binding_or_ceiling_changed')

    def reserve(self,intent,provider,model,bound,*,run_id,review_bound=0,pair_intent=None):
        proof=self.attest();reserved=amount(bound);day=proof['utc_day']
        with self.state.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM cf_intents WHERE id=?',(intent,)).fetchone():
                raise IntentExists('cloudflare_intent_already_exists')
            if db.execute('SELECT 1 FROM cf_revisions WHERE parent_id=?',(run_id,)).fetchone():
                raise ImageError('superseded_parent_dispatch_forbidden')
            slots=self.slot(db,run_id)
            if slots and slots[0]!=day:raise ImageError('article_slot_utc_day_changed')
            if slots:
                if model == self.config.image.model_cover or model == self.config.image.model_body:
                    if amount(review_bound) != amount((32768*40909+1024*290909)/1e6) or pair_intent:
                        raise ImageError('article_generation_review_pair_required')
                elif model != self.config.image.review_model or not pair_intent or review_bound:
                    raise ImageError('article_review_pair_required')
            if pair_intent:
                hold=db.execute('SELECT run_id,day,reserved FROM cf_review_holds WHERE intent=?',(pair_intent,)).fetchone()
                generation=db.execute('SELECT status FROM cf_intents WHERE id=?',(pair_intent,)).fetchone()
                if not hold or tuple(hold[:2])!=(run_id,day) or reserved>hold[2] or not generation or generation[0] not in ('settled','estimated'):
                    raise ImageError('review_pair_binding_changed')
                db.execute('DELETE FROM cf_review_holds WHERE intent=?',(pair_intent,))
                db.execute('INSERT OR IGNORE INTO cf_review_links VALUES(?,?,?,?,?)',(intent,pair_intent,run_id,day,hold[2]))
                if tuple(db.execute('SELECT generation,run_id,day,reserved FROM cf_review_links WHERE intent=?',(intent,)).fetchone()) != (pair_intent,run_id,day,hold[2]):
                    raise ImageError('review_pair_binding_changed')
            pair=amount(review_bound)
            baseline=db.execute('SELECT amount FROM cf_baselines WHERE day=?',(day,)).fetchone()[0]
            consumed=db.execute("SELECT COALESCE(SUM(charged),0) FROM cf_intents WHERE day=? AND status IN ('settled','estimated')",(day,)).fetchone()[0]
            pending=db.execute("SELECT COALESCE(SUM(reserved),0) FROM cf_intents WHERE status IN ('pending','uncertain')").fetchone()[0]
            ceiling=db.execute('SELECT ceiling FROM cf_identity').fetchone()[0]
            _,family=self.family(db,run_id);marks=','.join('?' for _ in family)
            used=db.execute("SELECT COALESCE(SUM(CASE WHEN status IN ('settled','estimated') THEN charged ELSE reserved END),0) FROM cf_intents WHERE run_id IN ("+marks+")",family).fetchone()[0]
            run=db.execute('SELECT ceiling FROM cf_runs WHERE id=?',(run_id,)).fetchone()
            holds=db.execute('SELECT COALESCE(SUM(reserved),0) FROM cf_review_holds').fetchone()[0]
            own_holds=db.execute('SELECT COALESCE(SUM(reserved),0) FROM cf_review_holds WHERE run_id IN ('+marks+')',family).fetchone()[0]
            if db.execute('SELECT 1 FROM cf_day_slots WHERE day=?',(day,)).fetchone():
                other=self.other_usage(db,day)
                if other+(0 if slots else reserved+pair)>1000000:
                    raise ImageError('two_article_other_use_allowance_exceeded')
            if baseline+consumed+pending+holds+reserved+pair>ceiling or not run or used+own_holds+reserved+pair>run[0]:
                raise ImageError('cloudflare_free_neuron_cap_reached')
            db.execute('INSERT INTO cf_intents(id,run_id,model,day,reserved,status) VALUES(?,?,?,?,?,?)',
                       (intent,run_id,model,day,reserved,'pending'))
            if pair:
                db.execute('INSERT INTO cf_review_holds VALUES(?,?,?,?)',(intent,run_id,day,pair))

    def assert_dispatch_day(self,run_id):
        self.attest()
        with self.state.connection() as db:
            if db.execute('SELECT 1 FROM cf_revisions WHERE parent_id=?',(run_id,)).fetchone():
                raise ImageError('superseded_parent_dispatch_forbidden')
            slot=self.slot(db,run_id)
            if slot and slot[0]!=utc_day(self.clock()):
                raise ImageError('article_slot_utc_day_changed')

    def cancel_undispatched(self,intent):
        """One atomic audit/release restores a review's original generation hold."""
        with self.state.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM cf_intents WHERE id=?',(intent,)).fetchone()
            if not row:return False
            if row['status']!='pending':raise ValueError('only_undispatched_pending_intent_can_be_cancelled')
            link=db.execute('SELECT generation,run_id,day,reserved FROM cf_review_links WHERE intent=?',(intent,)).fetchone()
            if link:
                gen=db.execute('SELECT status FROM cf_intents WHERE id=?',(link[0],)).fetchone()
                if not gen or gen[0] not in ('settled','estimated'):raise ImageError('review_pair_binding_changed')
                db.execute('INSERT INTO cf_review_holds VALUES(?,?,?,?)',tuple(link))
            else:
                db.execute('DELETE FROM cf_review_holds WHERE intent=?',(intent,))
            db.execute('''CREATE TABLE IF NOT EXISTS image_dispatch_cancellations(
                sequence INTEGER PRIMARY KEY, ledger_table TEXT NOT NULL, intent TEXT NOT NULL,
                at REAL NOT NULL, reason TEXT NOT NULL, reservation TEXT NOT NULL)''')
            db.execute('INSERT INTO image_dispatch_cancellations(ledger_table,intent,at,reason,reservation) VALUES(?,?,?,?,?)',
                       ('cf_intents',intent,self.clock(),'workflow_paused_before_dispatch',json.dumps(dict(row),sort_keys=True)))
            db.execute('DELETE FROM cf_intents WHERE id=?',(intent,))
            return True

    def settle(self,intent,units,charged,metadata=None):
        metadata=metadata or {};value=amount(charged)
        with self.state.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM cf_intents WHERE id=?',(intent,)).fetchone()
            if not row or row['status']!='pending' or value>row['reserved']:
                raise ImageError('cloudflare_usage_exceeds_reservation')
            status='estimated' if metadata.get('estimated') else 'settled'
            db.execute('UPDATE cf_intents SET status=?,charged=?,metadata=? WHERE id=?',
                       (status,value,json.dumps(metadata),intent))

    def uncertain(self,intent):
        with self.state.connection() as db:
            db.execute("UPDATE cf_intents SET status='uncertain' WHERE id=? AND status='pending'",(intent,))

    def get(self,intent):
        with self.state.connection() as db:
            row=db.execute('SELECT * FROM cf_intents WHERE id=?',(intent,)).fetchone()
            return dict(row) if row else None

    def report(self,run_id):
        with self.state.connection() as db:
            _,family=self.family(db,run_id);marks=','.join('?' for _ in family)
            return [dict(row)|{'unit':'milli-neurons','account_id':self.config.cloudflare.account_id}
                    for row in db.execute('SELECT * FROM cf_intents WHERE run_id IN ('+marks+') ORDER BY rowid',family)]

    def other_usage(self,db,day):
        """Baseline and liabilities outside this UTC day's committed slot runs."""
        baseline=db.execute('SELECT COALESCE(SUM(amount),0) FROM cf_baselines WHERE day=?',(day,)).fetchone()[0]
        value=db.execute("""SELECT COALESCE(SUM(CASE WHEN status IN ('pending','uncertain') THEN reserved ELSE charged END),0)
            FROM cf_intents WHERE (day=? OR status IN ('pending','uncertain')) AND run_id NOT IN
            (SELECT run_id FROM cf_day_slots WHERE day=? AND run_id IS NOT NULL
             UNION SELECT r.child_id FROM cf_revisions r JOIN cf_day_slots s ON s.run_id=r.parent_id WHERE s.day=?)""",(day,day,day)).fetchone()[0]
        holds=db.execute("""SELECT COALESCE(SUM(reserved),0) FROM cf_review_holds WHERE run_id NOT IN
            (SELECT run_id FROM cf_day_slots WHERE day=? AND run_id IS NOT NULL
             UNION SELECT r.child_id FROM cf_revisions r JOIN cf_day_slots s ON s.run_id=r.parent_id WHERE s.day=?)""",(day,day)).fetchone()[0]
        return baseline+value+holds

    def release_pair(self,intent):
        """Only a known non-dispatched/finished generation can abandon its review."""
        with self.state.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT status FROM cf_intents WHERE id=?',(intent,)).fetchone()
            if row and row[0] in ('pending','uncertain'):
                raise ImageError('unknown_generation_review_hold_retained')
            db.execute('DELETE FROM cf_review_holds WHERE intent=?',(intent,))

    def abandon_unstarted_review(self,intent,*,run_id,binding,manifest_path,manifest_sha,
                                owner_receipt_path,owner_receipt_sha,apply=False):
        """One explicit staging decision abandons a review, never its uncertain generation."""
        c=self.config;root=Path(_RECON_ROOT)
        if (c.mode!='live' or c.contract.environment!='staging' or not c.contract.verified or c.db_path!=str(root/'nailblog.sqlite3') or
                c.slots!=('17:45',) or c.contract.site_origin!='https://staging.nailhub.ai' or
                c.contract.api_origin!='https://api-staging.nailhub.ai' or
                c.contract.media_origin!='https://vlink-storages-test.sgp1.cdn.digitaloceanspaces.com' or
                c.image.provider!='cloudflare' or not c.cloudflare.two_article_free_day or
                c.cloudflare.daily_neurons!=10000 or self.state.path!=c.cloudflare.quota_db_path or
                (run_id,binding,intent)!=(_RECON_RUN,_RECON_BINDING,_RECON_INTENT)):
            raise ImageError('staging_review_abandonment_scope_invalid')
        def read_private(value,limit,modes):
            p=Path(value)
            if (not p.is_absolute() or not p.is_file() or any(x.is_symlink() for x in (p,*p.parents)) or
                    p.stat().st_size>limit or (os.name=='posix' and
                    (p.stat().st_uid!=os.getuid() or p.stat().st_mode&0o777 not in modes))):
                raise ImageError('private_reconciliation_evidence_required')
            return p.read_bytes()
        mp=Path(manifest_path);receipt_path=Path(owner_receipt_path)
        if mp.resolve()!=(root/'run/images/manifest.json').resolve() or receipt_path.parent.resolve()!=root.resolve():
            raise ImageError('reconciliation_evidence_path_changed')
        raw=read_private(mp,4*1024*1024,(0o600,0o640));receipt_raw=read_private(receipt_path,16384,(0o600,))
        if hashlib.sha256(raw).hexdigest()!=manifest_sha or hashlib.sha256(receipt_raw).hexdigest()!=owner_receipt_sha:
            raise ImageError('reconciliation_evidence_sha_changed')
        receipt=json.loads(receipt_raw);manifest=json.loads(raw)
        expected={'kind':'nailblog_abandon_unstarted_review_v1','owner':'AlexChu','action':'abandon_only_unstarted_review',
                  'image_run_id':run_id,'image_binding':binding,'intent':intent,'manifest_sha256':manifest_sha,
                  'generation_reserved_milli':156300,'unstarted_review_reserved_milli':1638397,
                  'generation_must_remain_uncertain':True,'expires_at':'2026-10-20T11:00:00+00:00'}
        if any(receipt.get(k)!=v or type(receipt.get(k)) is not type(v) for k,v in expected.items()):
            raise ImageError('owner_review_abandonment_decision_invalid')
        try:
            created=datetime.fromisoformat(receipt['created_at_utc'].replace('Z','+00:00'))
            expires=datetime.fromisoformat(receipt['expires_at'])
            if created.utcoffset() is None or not created.timestamp()<=self.clock()+30<expires.timestamp():raise ValueError()
        except (ValueError,KeyError,TypeError):raise ImageError('owner_review_abandonment_decision_expired') from None
        candidate=manifest.get('candidates',{}).get('body-1-r0-c0',{})
        if (manifest.get('run_id')!=run_id or manifest.get('binding')!=binding or manifest.get('status')!='held' or
                manifest.get('error')!='cloudflare_rate_limited' or candidate.get('intent')!=intent or
                candidate.get('status')!='pending' or any(candidate.get(k) for k in ('file','sha','review_intent')) or
                manifest.get('positions',{}).get('body-1') or any(mp.parent.glob('*-body-1-r0-c0.*'))):
            raise ImageError('unstarted_review_manifest_proof_required')
        with self.state.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'cf_manual_review_abandonments' in tables and db.execute('SELECT 1 FROM cf_manual_review_abandonments WHERE intent=?',(intent,)).fetchone():
                raise ImageError('review_abandonment_already_recorded')
            original=db.execute('SELECT * FROM cf_intents WHERE id=?',(intent,)).fetchone()
            hold=db.execute('SELECT * FROM cf_review_holds WHERE intent=?',(intent,)).fetchone()
            run=db.execute('SELECT binding,ceiling FROM cf_runs WHERE id=?',(run_id,)).fetchone()
            slot=self.slot(db,run_id)
            if (not original or original['run_id']!=run_id or original['status']!='uncertain' or original['reserved']!=156300 or
                    original['charged']!=0 or original['model']!=c.image.model_body or not hold or
                    (hold['run_id'],hold['day'],hold['reserved'])!=(run_id,utc_day(self.clock()),1638397) or
                    original['day']!=hold['day'] or not run or tuple(run)!=(binding,4500000) or
                    not slot or slot['day']!=hold['day'] or slot['ceiling']!=4500000 or
                    db.execute('SELECT 1 FROM cf_review_links WHERE generation=?',(intent,)).fetchone() or
                    [r[0] for r in db.execute("SELECT id FROM cf_intents WHERE status IN ('pending','uncertain')")]!=[intent]):
                raise ImageError('unstarted_review_ledger_proof_required')
            original=dict(original);hold=dict(hold)
            preserved={t:[tuple(r) for r in db.execute('SELECT * FROM '+t+' ORDER BY rowid')]
                       for t in ('cf_intents','cf_runs','cf_day_slots','cf_baselines','cf_identity','cf_revisions','cf_review_links')}
            other_holds=[tuple(r) for r in db.execute('SELECT * FROM cf_review_holds WHERE intent<>? ORDER BY rowid',(intent,))]
            result={'verified':True,'applied':bool(apply),'intent':intent,'generation_status':'uncertain',
                    'generation_reserved_milli_retained':156300,'unstarted_review_reserved_milli':1638397,'provider_calls':False}
            if not apply:return result
            # Schema creation and audit insertion are part of the same apply-only transaction.
            db.execute('''CREATE TABLE IF NOT EXISTS cf_manual_review_abandonments(
                intent TEXT PRIMARY KEY,run_id TEXT NOT NULL,binding TEXT NOT NULL,day TEXT NOT NULL,
                owner_receipt_sha TEXT NOT NULL,manifest_sha TEXT NOT NULL,
                original_intent TEXT NOT NULL,original_hold TEXT NOT NULL,at REAL NOT NULL)''')
            db.execute('INSERT INTO cf_manual_review_abandonments VALUES(?,?,?,?,?,?,?,?,?)',
                       (intent,run_id,binding,hold['day'],owner_receipt_sha,manifest_sha,
                        json.dumps(original,sort_keys=True),json.dumps(hold,sort_keys=True),self.clock()))
            if db.execute('DELETE FROM cf_review_holds WHERE intent=?',(intent,)).rowcount!=1:
                raise ImageError('review_abandonment_hold_changed')
            if any([tuple(r) for r in db.execute('SELECT * FROM '+t+' ORDER BY rowid')]!=rows for t,rows in preserved.items()):
                raise ImageError('review_abandonment_touched_generation_or_history')
            if [tuple(r) for r in db.execute('SELECT * FROM cf_review_holds ORDER BY rowid')]!=other_holds:
                raise ImageError('review_abandonment_touched_other_holds')
            return result

    def abandon_second_unstarted_review(self,intent,*,run_id,binding,manifest_path,manifest_sha,
                                       ledger_evidence_path,ledger_evidence_sha,
                                       owner_receipt_path,owner_receipt_sha,apply=False):
        """Separate Owner decision: abandon only r1 review, retaining both unknown generations."""
        c=self.config;root=Path(_RECON_ROOT);day='2026-10-20'
        if (c.mode!='live' or not c.contract.verified or c.contract.environment!='staging' or
                c.db_path!=str(root/'nailblog.sqlite3') or c.slots!=('17:45',) or
                c.contract.site_origin!='https://staging.nailhub.ai' or
                c.contract.api_origin!='https://api-staging.nailhub.ai' or
                c.contract.media_origin!='https://vlink-storages-test.sgp1.cdn.digitaloceanspaces.com' or
                c.cloudflare.account_id!='237230e9c6de6d15e31c12a93f70a9a7' or
                c.cloudflare.quota_db_path!=_SECOND_QUOTA or self.state.path!=_SECOND_QUOTA or
                c.image.provider!='cloudflare' or not c.cloudflare.two_article_free_day or
                c.cloudflare.daily_neurons!=10000 or utc_day(self.clock())!=day or
                c.image.model_body!='@cf/black-forest-labs/flux-2-klein-4b' or
                (run_id,binding,intent)!=(_RECON_RUN,_RECON_BINDING,_SECOND_INTENT) or
                manifest_sha!=_SECOND_MANIFEST_SHA or ledger_evidence_sha!=_SECOND_EVIDENCE_SHA):
            raise ImageError('second_review_abandonment_scope_invalid')
        def read_private(value,limit,modes):
            p=Path(value)
            if (not p.is_absolute() or not p.is_file() or any(x.is_symlink() for x in (p,*p.parents)) or
                    not 0<p.stat().st_size<=limit or (os.name=='posix' and
                    (p.stat().st_uid!=os.getuid() or p.stat().st_mode&0o777 not in modes))):
                raise ImageError('private_second_reconciliation_evidence_required')
            return p.read_bytes()
        mp,ep,rp=Path(manifest_path),Path(ledger_evidence_path),Path(owner_receipt_path)
        if (mp.resolve()!=(root/'run/images/manifest.json').resolve() or
                any(p.parent.resolve()!=root.resolve() for p in (ep,rp)) or ep==rp):
            raise ImageError('second_reconciliation_evidence_path_changed')
        raw=read_private(mp,4*1024*1024,(0o600,0o640));evidence_raw=read_private(ep,256*1024,(0o600,0o640))
        receipt_raw=read_private(rp,16384,(0o600,))
        if (hashlib.sha256(raw).hexdigest()!=manifest_sha or
                hashlib.sha256(evidence_raw).hexdigest()!=ledger_evidence_sha or
                hashlib.sha256(receipt_raw).hexdigest()!=owner_receipt_sha):
            raise ImageError('second_reconciliation_evidence_sha_changed')
        manifest,evidence,receipt=json.loads(raw),json.loads(evidence_raw),json.loads(receipt_raw)
        expected={'kind':'nailblog_abandon_second_unstarted_review_v1','owner':'AlexChu',
                  'action':'abandon_only_second_unstarted_review','environment':'staging',
                  'account_id':c.cloudflare.account_id,'config_db_path':c.db_path,'quota_db_path':_SECOND_QUOTA,
                  'image_run_id':run_id,'image_binding':binding,'intent':intent,
                  'generation_intents_retained':[_RECON_INTENT,_SECOND_INTENT],
                  'manifest_sha256':manifest_sha,'ledger_evidence_sha256':ledger_evidence_sha,
                  'generation_reserved_milli':156300,'total_uncertain_generation_reserved_milli':312600,
                  'unstarted_review_reserved_milli':1638397,'both_generations_must_remain_uncertain':True,
                  'expires_at':'2026-10-20T13:00:00Z'}
        if any(receipt.get(k)!=v or type(receipt.get(k)) is not type(v) for k,v in expected.items()):
            raise ImageError('owner_second_review_decision_invalid')
        try:
            created=datetime.fromisoformat(receipt['created_at_utc'].replace('Z','+00:00'))
            expires=datetime.fromisoformat(receipt['expires_at'].replace('Z','+00:00'))
            result=evidence['result'];observed=datetime.fromisoformat(result['at'].replace('Z','+00:00'))
            if (created.utcoffset() is None or observed.utcoffset() is None or
                    not observed.timestamp()<=created.timestamp()+30<=self.clock()+60 or
                    not created.timestamp()<=self.clock()+30<expires.timestamp() or
                    utc_day(observed.timestamp())!=day or evidence['exit']!=0 or
                    result['writes']!=0 or result['provider_calls']!=0 or result['ledger_bytes_unchanged'] is not True):raise ValueError()
            rows=result['rows']
        except (KeyError,ValueError,TypeError):
            raise ImageError('owner_second_review_decision_or_evidence_expired') from None
        unknowns=[{'id':i,'run_id':run_id,'model':c.image.model_body,'day':day,
                   'reserved':156300,'charged':0,'status':'uncertain','metadata':'{}'}
                  for i in (_RECON_INTENT,_SECOND_INTENT)]
        hold_expected={'intent':intent,'run_id':run_id,'day':day,'reserved':1638397}
        candidate=manifest.get('candidates',{}).get('body-1-r1-c0',{})
        if (manifest.get('run_id')!=run_id or manifest.get('binding')!=binding or manifest.get('status')!='held' or
                manifest.get('error')!='cloudflare_rate_limited' or candidate.get('intent')!=intent or
                candidate.get('status')!='pending' or any(candidate.get(k) for k in ('file','sha','review_intent')) or
                manifest.get('positions',{}).get('body-1') or any(mp.parent.glob('*-body-1-r1-c0.*')) or
                rows.get('cf_intents')!=unknowns or rows.get('cf_review_holds')!=[hold_expected] or
                rows.get('manifest',{}).get('sha256')!=manifest_sha or
                rows.get('manifest',{}).get('path')!=str(mp) or
                rows.get('manifest',{}).get('pending_candidates',{}).get('body-1-r1-c0')!=candidate or
                any(r.get('generation')==intent for r in rows.get('cf_review_links',[]))):
            raise ImageError('second_unstarted_review_manifest_proof_required')
        with self.state.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            if ('cf_second_review_abandonments' in tables and
                    db.execute('SELECT 1 FROM cf_second_review_abandonments WHERE intent=?',(intent,)).fetchone()):
                raise ImageError('second_review_abandonment_already_recorded')
            current=[dict(r) for r in db.execute("SELECT * FROM cf_intents WHERE status IN ('pending','uncertain') ORDER BY id")]
            hold=db.execute('SELECT * FROM cf_review_holds WHERE intent=?',(intent,)).fetchone()
            prior_rows=[dict(r) for r in db.execute('SELECT * FROM cf_manual_review_abandonments')] if 'cf_manual_review_abandonments' in tables else []
            prior=prior_rows[0] if len(prior_rows)==1 else {}
            run=db.execute('SELECT binding,ceiling FROM cf_runs WHERE id=?',(run_id,)).fetchone();slot=self.slot(db,run_id)
            links=[dict(r) for r in db.execute('SELECT * FROM cf_review_links ORDER BY rowid')]
            prior_hold={'intent':_RECON_INTENT,'run_id':run_id,'day':day,'reserved':1638397}
            if (current!=unknowns or not hold or dict(hold)!=hold_expected or
                    not run or tuple(run)!=(binding,4500000) or not slot or slot['day']!=day or slot['ceiling']!=4500000 or
                    db.execute('SELECT 1 FROM cf_review_holds WHERE intent=?',(_RECON_INTENT,)).fetchone() or
                    any(r['generation']==intent for r in links) or links!=rows.get('cf_review_links') or
                    prior.get('intent')!=_RECON_INTENT or prior.get('run_id')!=run_id or
                    prior.get('binding')!=binding or prior.get('day')!=day or
                    json.loads(prior.get('original_intent','null'))!=unknowns[0] or
                    json.loads(prior.get('original_hold','null'))!=prior_hold):
                raise ImageError('second_unstarted_review_ledger_proof_required')
            # Preserve every existing table, including the first manual audit.
            preserved={t:[tuple(r) for r in db.execute('SELECT * FROM "'+t.replace('"','""')+'" ORDER BY rowid')]
                       for t in tables if t!='cf_review_holds'}
            other_holds=[tuple(r) for r in db.execute('SELECT * FROM cf_review_holds WHERE intent<>? ORDER BY rowid',(intent,))]
            outcome={'verified':True,'applied':bool(apply),'intent':intent,
                     'uncertain_generation_reserved_milli_retained':312600,
                     'unstarted_review_reserved_milli':1638397,'provider_calls':False}
            if not apply:return outcome
            db.execute('''CREATE TABLE IF NOT EXISTS cf_second_review_abandonments(
                intent TEXT PRIMARY KEY,run_id TEXT NOT NULL,binding TEXT NOT NULL,day TEXT NOT NULL,
                owner_receipt_sha TEXT NOT NULL,manifest_sha TEXT NOT NULL,ledger_evidence_sha TEXT NOT NULL,
                original_unknowns TEXT NOT NULL,original_hold TEXT NOT NULL,prior_audit TEXT NOT NULL,at REAL NOT NULL)''')
            db.execute('INSERT INTO cf_second_review_abandonments VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (intent,run_id,binding,day,owner_receipt_sha,manifest_sha,ledger_evidence_sha,
                        json.dumps(current,sort_keys=True),json.dumps(dict(hold),sort_keys=True),json.dumps(prior_rows,sort_keys=True),self.clock()))
            if db.execute('DELETE FROM cf_review_holds WHERE intent=?',(intent,)).rowcount!=1:
                raise ImageError('second_review_abandonment_hold_changed')
            if any([tuple(r) for r in db.execute('SELECT * FROM "'+t.replace('"','""')+'" ORDER BY rowid')]!=v for t,v in preserved.items()):
                raise ImageError('second_review_abandonment_touched_history')
            if [tuple(r) for r in db.execute('SELECT * FROM cf_review_holds ORDER BY rowid')]!=other_holds:
                raise ImageError('second_review_abandonment_touched_other_holds')
            return outcome

    def forecast_report(self,run_id):
        day=utc_day(self.clock())
        with self.state.connection() as db:
            root,family=self.family(db,run_id);marks=','.join('?' for _ in family)
            rows=db.execute("SELECT * FROM cf_intents WHERE run_id IN ("+marks+")",family).fetchall()
            actual=sum(r['charged'] for r in rows if r['status'] in ('settled','estimated'))
            pending=sum(r['reserved'] for r in rows if r['status'] in ('pending','uncertain'))
            holds=db.execute('SELECT COALESCE(SUM(reserved),0) FROM cf_review_holds WHERE run_id IN ('+marks+')',family).fetchone()[0]
            run=db.execute('SELECT ceiling FROM cf_runs WHERE id=?',(run_id,)).fetchone()
            committed=db.execute('SELECT COALESCE(SUM(ceiling),0) FROM cf_day_slots WHERE day=?',(day,)).fetchone()[0]
            return {'unit':'neurons','utc_day':day,'family_root':root,'family_runs':family,'run_charged_or_estimated':actual/1000,
                    'run_pending_or_uncertain':pending/1000,'run_review_holds':holds/1000,
                    'run_headroom':(run[0]-actual-pending-holds)/1000 if run else None,
                    'day_slot_commitments':committed/1000,'day_other_usage':self.other_usage(db,day)/1000,
                    'other_allowance_headroom':(1000000-self.other_usage(db,day))/1000 if committed else None,
                    'quality_completion_guaranteed':False}
