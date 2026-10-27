"""Offline protocol/security tests. Fixtures are never native runtime acceptance."""
import asyncio
from dataclasses import replace, asdict
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

from nailblog.config import SubscriptionConfig, ImageConfig, ContractConfig, _subscription
from nailblog._errors import ConfigError
from nailblog.images import ImageError
from nailblog.images.agy_native import native_image, native_call, fields
from nailblog.images.subscription import (Worker, SubscriptionImages, atomic, checked, read, verify_receipt,
                                         config_identity, validate_request, PROTOCOL, POLICY_SHA)
from nailblog.images.pipeline import ImagePipeline, DispatchPaused
from nailblog.images.post import crop, decode
from nailblog.publication import digest
from nailblog.onepost import OnePost
from nailblog.publication import AdminError
from nailblog.approvals import ApprovalStore, build_preview
from nailblog.service import Service
from tests.helpers import settings
from tests.test_real_images import article, fixture_image, fixture_cover
from tests.test_config_autoflow import live_sections, LIVE
from tests.helpers import load


def current_user():
    if os.name == 'nt': return 'unused'
    import pwd
    return pwd.getpwuid(os.getuid()).pw_name


def config(root):
    queue = Path(root) / 'queue'; queue.mkdir(mode=0o700)
    private = Path(root) / 'private'; private.mkdir(mode=0o700)
    return replace(settings(root), image=ImageConfig(provider='subscription', review_bypass=True, candidates=1, max_rounds=1),
        contract=ContractConfig(environment='staging'),
        subscription=SubscriptionConfig(queue_dir=str(queue), private_dir=str(private),
                                        worker_user=current_user(), producer_user=current_user()))


def request(cfg, *, prompt='safe prompt', binding='b'*64, run_id='run', position='cover'):
    job_id=digest({'protocol':PROTOCOL,'binding':binding,'run_id':run_id,'position':position,'prompt':prompt})
    return {'protocol': PROTOCOL, 'job_id': job_id, 'binding': 'b'*64, 'article_sha': 'c'*64,
            'prompt': prompt, 'image_name': 'img_'+job_id[:20], 'expires_at': 1000,
            'run_id': run_id, 'config': config_identity(cfg), 'position':position}


def integer(value):
    out=bytearray()
    while value>=128: out.append((value&127)|128); value>>=7
    out.append(value)
    return bytes(out)


def proto(**values):
    out=b''
    for number,value in values.items():
        number=int(number[1:])
        if isinstance(value,int): out+=integer(number<<3)+integer(value)
        else: out+=integer((number<<3)|2)+integer(len(value))+value
    return out


def native_record(req, session, source):
    call=proto(f1=b'call_1',f2=b'generate_image',f3=json.dumps({'Prompt':req['prompt'],'ImageName':req['image_name'],'AspectRatio':'16:9'}).encode())
    generated=proto(f1=req['prompt'].encode(),f4=req['image_name'].encode(),f5=b'gemini-3.1-flash-image',
        f6=proto(f1=b'image/png',f5=source.encode()),f7=b'16:9')
    step=proto(f1=91,f4=3,f104=generated)
    return proto(f1=132,f4=3,f5=proto(f4=call),
                 f140=proto(f2=proto(f6=proto(f1=b'type.googleapis.com/gemini_coder.Step',f2=step))))


def invocation_record(req, call_id=b'call_1', tool=b'generate_image'):
    call=proto(f1=call_id,f2=tool,f3=json.dumps({'Prompt':req['prompt'],'ImageName':req['image_name'],'AspectRatio':'16:9'}).encode())
    return proto(f1=15,f4=3,f5=b'',f20=proto(f7=call))


def receipt(req, raw):
    return {'protocol': PROTOCOL, 'job_id': req['job_id'], 'request_sha': digest(req), 'status': 'complete',
            'conversation_id': 'f6ae7cf1-c230-4bb1-a2e1-c1753523ffbb', 'tool_step': 4, 'tool_call_id': 'call_1',
            'native_record_sha': 'e'*64, 'source_sha': hashlib.sha256(raw).hexdigest(), 'source_size': list(decode(raw).size),
            'native_call_sha': 'd'*64, 'policy_sha': POLICY_SHA,
            'native_columns': {'permissions': None, 'error_details': None},
            'cli_version': '1.2.11', 'agent_model': 'gemini-3.8-flash-medium', 'effort': 'medium',
            'backend_model': 'gemini-3.1-flash-image',
            'billing': {'route': 'subscription', 'api_usd': None, 'plan_cost_included': 'unknown'}}


class ConfigTests(unittest.TestCase):
    def test_cli_schema_is_pinned_and_rejects_credential_or_permission_knobs(self):
        for changes in ({'version':'1.4.7'}, {'executable':'agy'}, {'effort':'high'}, {'agent_model':'other'},
                        {'worker_user':'tgnailblog'}, {'dangerously_skip_permissions':True}, {'api_key':'x'}):
            with self.subTest(changes=changes), self.assertRaises(ConfigError): _subscription(changes)

    def test_full_live_staging_config_without_api_key_or_image_prices(self):
        with tempfile.TemporaryDirectory() as directory:
            sections = live_sections(image='provider="subscription"\ncandidates=1\nmax_rounds=1\nreview_bypass=true',
                subscription='queue_dir="/var/lib/nailblog-subscription/queue"\nprivate_dir="/home/startechai/snap/antigravity-cli/common/nailblog-worker"',
                **{'budget__image_unit_prices__m-cover':None,'budget__image_unit_prices__m-body':None})
            c = load(directory, LIVE, **sections)
            self.assertEqual(c.image.provider, 'subscription')
            self.assertEqual(c.image.key_file, '')
            for text in ('provider="subscription"\nreview_bypass=true',
                         'provider="subscription"\ncandidates=1\nmax_rounds=1',
                         'provider="subscription"\ncandidates=1\nmax_rounds=1\nreview_bypass=true\nkey_source="env"'):
                with self.assertRaises(ConfigError): load(directory, LIVE, **{**sections,'image':text})


class NativeTests(unittest.TestCase):
    def setUp(self):
        self.raw = (Path(__file__).parent/'fixtures/subscription/agy-1.2.11-generate-image.bin').read_bytes()
        self.kw = {'conversation':'f6ae7cf1-c230-4bb1-a2e1-c1753523ffbb', 'image_name':'cover',
            'prompt':'Photorealistic editorial cover image showing elegant autumn nail art, clean salon background, no text, no logos, no watermark',
            'brain':'/home/startechai/snap/antigravity-cli/common/.gemini/antigravity-cli/brain'}

    def test_real_saved_native_record_has_separate_reported_backend(self):
        self.assertEqual(hashlib.sha256(self.raw).hexdigest(),'ea741c8eded70e1ccd412e17e353e0590384615c22a05848365f4a56be019f40')
        result = native_image(self.raw, **self.kw)
        self.assertEqual(result['backend_model'], 'gemini-3.1-flash-image')
        self.assertEqual(result['tool_call_id'], 'call_1748796')
        self.assertTrue(result['source'].endswith('cover_1792981647754.jpg'))

    def test_prompt_session_backend_unknownwire_and_arbitrary_prose_are_rejected(self):
        for changes in ({'prompt':'other'}, {'conversation':'00000000-0000-0000-0000-000000000000'}, {'image_name':'other'}):
            with self.subTest(changes=changes), self.assertRaises(ImageError): native_image(self.raw, **{**self.kw, **changes})
        for raw in (self.raw[:-1], self.raw.replace(b'gemini-3.1-flash-image', b'gemini-3.2-flash-image'), b'SUCCESS /tmp/image.jpg',
                    b'\xff'*32, self.raw+b'\x08\x84\x01'):
            with self.assertRaises(ImageError): native_image(raw, **self.kw)
        with self.assertRaises(ImageError): fields(b'x'*(2*1024*1024+1))


class PathTests(unittest.TestCase):
    def test_traversal_bounds_hardlinks_and_symlink_ancestors(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); file=root/'real'; file.write_bytes(b'x'); file.chmod(0o600)
            with self.assertRaises(ImageError): checked(root/'..'/'real')
            with self.assertRaises(ImageError): read(file,limit=0)
            try: (root/'link').symlink_to(file)
            except OSError: return
            with self.assertRaises(ImageError): read(root/'link')
            (root/'dirlink').symlink_to(root, target_is_directory=True)
            with self.assertRaises(ImageError): read(root/'dirlink'/'real')
            try: os.link(file, root/'hard')
            except OSError: return
            with self.assertRaises(ImageError): read(file)

    @unittest.skipUnless(os.name=='posix','POSIX ownership and mode contract')
    def test_world_access_and_wrong_uid_rejected_but_native_readonly_public_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'x'; path.write_bytes(b'x'); path.chmod(0o644)
            with self.assertRaises(ImageError): read(path)
            self.assertEqual(read(path,native=True),b'x')
            with self.assertRaises(ImageError): read(path,native=True,owner=os.getuid()+1)
            path.chmod(0o666)
            with self.assertRaises(ImageError): read(path,native=True)


class QueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_receipt_needed_empty_success_is_not_enough_and_timeout_reuses_job(self):
        with tempfile.TemporaryDirectory() as directory:
            c=config(directory); now=[0]
            async def wait(_): now[0]+=1000
            provider=SubscriptionImages(c,clock=lambda:now[0],sleep=wait)
            atomic(Path(c.subscription.queue_dir)/'worker-ready.json',{'protocol':PROTOCOL,'config':config_identity(c.subscription),'at':0})
            kwargs={'binding':'b'*64,'article_sha':'c'*64,'prompt':'nail swatches','run_id':'run','position':'cover'}
            with self.assertRaisesRegex(ImageError,'unknown_no_retry'): await provider.generate(**kwargs)
            count=len([p for p in Path(c.subscription.queue_dir).iterdir() if len(p.name)==64])
            with self.assertRaisesRegex(ImageError,'unknown_no_retry'): await provider.generate(**kwargs)
            self.assertEqual(len([p for p in Path(c.subscription.queue_dir).iterdir() if len(p.name)==64]),count)
            self.assertEqual(count,1)

    async def test_cancel_before_submission_creates_no_job(self):
        with tempfile.TemporaryDirectory() as directory:
            c=config(directory)
            def denied(): raise ImageError('paused')
            with self.assertRaisesRegex(ImageError,'paused'):
                await SubscriptionImages(c).generate(binding='b'*64,article_sha='c'*64,prompt='swatches',run_id='run',position='cover',before_dispatch=denied)
            self.assertEqual(list(Path(c.subscription.queue_dir).iterdir()),[])

    def test_failed_receipt_wrongsha_zero_bill_and_stale_request_rejected(self):
        cfg=SubscriptionConfig(); req=request(cfg); raw=fixture_image(size=(1376,768)); valid=receipt(req,raw)
        verify_receipt(valid,req,raw)
        for change in ({'status':'unknown'},{'source_sha':'f'*64},{'backend_model':'agent_model'},
                       {'billing':{'route':'subscription','api_usd':0,'plan_cost_included':'unknown'}},
                       {'request_sha':'d'*64}):
            with self.subTest(change=change), self.assertRaises(ImageError): verify_receipt({**valid,**change},req,raw)
        with self.assertRaises(ImageError): validate_request({**req,'config':{'version':'other'}},req['job_id'],cfg)


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    async def make(self,directory):
        c=config(directory); calls=[]
        class Broker:
            async def generate(self,**kw):
                calls.append(kw)
                req=request(c.subscription,prompt=kw['prompt'],binding=kw['binding'],run_id=kw['run_id'],position=kw['position'])
                req.update(binding=kw['binding'],article_sha=kw['article_sha'])
                job_id=req['job_id']
                raw=fixture_image(len(calls),size=(1376,768)); rec=receipt(req,raw)
                native=native_record(req,rec['conversation_id'],'file:///home/'+c.subscription.worker_user+'/snap/antigravity-cli/common/.gemini/antigravity-cli/brain/'+rec['conversation_id']+'/native.jpg')
                rec['native_record_sha']=hashlib.sha256(native).hexdigest()
                call=invocation_record(req); rec['native_call_sha']=hashlib.sha256(call).hexdigest()
                base=Path(c.subscription.queue_dir)/job_id; base.mkdir(mode=0o700)
                atomic(base/'request.json',req); atomic(base/'receipt.json',rec);atomic(base/'native-record.bin',native,raw=True)
                atomic(base/'native-call.bin',call,raw=True)
                return raw,rec
        pipeline=ImagePipeline(c,provider=Broker(),cover_renderer=fixture_cover)
        return c,pipeline,calls

    async def test_pause_before_first_job_keeps_working_manifest_and_exact_run_resumes(self):
        with tempfile.TemporaryDirectory() as directory:
            c,pipeline,calls=await self.make(directory);out=Path(directory)/'out'
            def paused(): raise ImageError('owner_paused')
            pipeline.before_dispatch=paused
            with self.assertRaisesRegex(DispatchPaused,'workflow_paused_before_dispatch') as caught:
                await pipeline.run(article(),out,'run',None,subscription_enabled=True)
            self.assertFalse(caught.exception.dispatched)
            self.assertEqual(calls,[])
            self.assertEqual(list(Path(c.subscription.queue_dir).iterdir()),[])
            self.assertEqual(json.loads((out/'manifest.json').read_text())['status'],'working')
            pipeline.before_dispatch=None
            document=await pipeline.run(article(),out,'run',None,subscription_enabled=True)
            self.assertEqual(len(calls),4)
            self.assertTrue(OnePost.provenance(document,out,config=c))

    async def test_worker_not_ready_before_enqueue_is_resumable_without_creating_job(self):
        with tempfile.TemporaryDirectory() as directory:
            c,pipeline,calls=await self.make(directory);out=Path(directory)/'out';broker=pipeline.provider
            pipeline.provider=SubscriptionImages(c)
            with self.assertRaisesRegex(DispatchPaused,'cli_worker_not_ready_before_dispatch') as caught:
                await pipeline.run(article(),out,'run',None,subscription_enabled=True)
            self.assertFalse(caught.exception.dispatched)
            self.assertEqual(list(Path(c.subscription.queue_dir).iterdir()),[])
            self.assertEqual(json.loads((out/'manifest.json').read_text())['status'],'working')
            pipeline.provider=broker
            await pipeline.run(article(),out,'run',None,subscription_enabled=True)
            self.assertEqual(len(calls),4)

    async def test_pause_after_enqueue_and_unknown_job_stay_held_on_exact_resume(self):
        for mode in ('pause','timeout'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as directory:
                c,pipeline,calls=await self.make(directory);out=Path(directory)/'out';now=[0]
                atomic(Path(c.subscription.queue_dir)/'worker-ready.json',{'protocol':PROTOCOL,'config':config_identity(c.subscription),'at':0})
                async def wait(_): now[0]+=1000
                pipeline.provider=SubscriptionImages(c,clock=lambda:now[0],sleep=wait)
                if mode=='pause':
                    def paused_when_queued():
                        if any(len(p.name)==64 for p in Path(c.subscription.queue_dir).iterdir()):
                            raise ImageError('owner_paused')
                    pipeline.before_dispatch=paused_when_queued
                    with self.assertRaises(DispatchPaused) as caught:
                        await pipeline.run(article(),out,'run',None,subscription_enabled=True)
                    self.assertTrue(caught.exception.dispatched)
                else:
                    with self.assertRaisesRegex(ImageError,'unknown_no_retry'):
                        await pipeline.run(article(),out,'run',None,subscription_enabled=True)
                jobs=[p.name for p in Path(c.subscription.queue_dir).iterdir() if len(p.name)==64]
                self.assertEqual(len(jobs),1)
                self.assertEqual(json.loads((out/'manifest.json').read_text())['status'],'held')
                pipeline.before_dispatch=None
                with self.assertRaisesRegex(ImageError,'requires_manual_reconciliation'):
                    await pipeline.run(article(),out,'run',None,subscription_enabled=True)
                self.assertEqual([p.name for p in Path(c.subscription.queue_dir).iterdir() if len(p.name)==64],jobs)
                self.assertEqual(calls,[])

    async def test_finished_native_dimensions_receipts_cache_no_paid_intents_and_source_tamper(self):
        with tempfile.TemporaryDirectory() as directory:
            c,pipeline,calls=await self.make(directory); out=Path(directory)/'out'
            with patch('nailblog.paid.PaidLedger',side_effect=AssertionError('no API ledger')):
                document=await pipeline.run(article(),out,'run',None,subscription_enabled=True)
            self.assertEqual(len(calls),4)
            self.assertEqual(decode(Path(document['cover_path']).read_bytes()).size,(1376,768))
            self.assertEqual(document['image_source']['api_usd'],None)
            self.assertTrue(OnePost.provenance(document,out,config=c))
            self.assertEqual(await pipeline.run(article(),out,'run',None,subscription_enabled=True),document)
            self.assertEqual(len(calls),4)
            with pipeline.state.connection() as db:
                self.assertEqual(db.execute("SELECT count(*) FROM sqlite_master WHERE name='paid_intents'").fetchone()[0],0)
            (out/'body-1-source-r0.image').write_bytes(b'changed')
            with self.assertRaises(ImageError): await pipeline.run(article(),out,'run',None,subscription_enabled=True)

    async def test_wrong_enable_legacy_source_too_small_and_binding_change_failclosed(self):
        with tempfile.TemporaryDirectory() as directory:
            c,pipeline,calls=await self.make(directory); out=Path(directory)/'out'
            with self.assertRaises(ImageError): await pipeline.run(article(),out,'run',1,paid_enabled=True)
            self.assertEqual(calls,[])
            with self.assertRaisesRegex(ImageError,'resolution_insufficient'): crop(fixture_image(size=(1200,675)),(1376,768))
            await pipeline.run(article(),out,'run',None,subscription_enabled=True)
            other=replace(c,subscription=replace(c.subscription,timeout_seconds=120))
            with self.assertRaisesRegex(ImageError,'binding_changed'):
                await ImagePipeline(other,provider=pipeline.provider).run(article(),out,'run',None,subscription_enabled=True)

    async def test_owner_reject_all_positions_generates_fresh_bounded_attempt_and_invalidates_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            c,pipeline,calls=await self.make(directory); out=Path(directory)/'out'
            first=await pipeline.run(article(),out,'run',None,subscription_enabled=True)
            old_jobs={p.name for p in Path(c.subscription.queue_dir).iterdir()}
            for name,sha in zip(['cover','body-1','body-2','body-3'],first['image_hashes']):
                pipeline.reject_selected(out,name,sha,'Owner rejects first set')
            with self.assertRaises(AdminError): OnePost.provenance(first,out,config=c)
            second=await pipeline.run(article(),out,'run',None,subscription_enabled=True)
            self.assertEqual(len(calls),8)
            self.assertEqual([call['position'] for call in calls[4:]],['cover-r1','body-1-r1','body-2-r1','body-3-r1'])
            new_jobs={p.name for p in Path(c.subscription.queue_dir).iterdir()}-old_jobs
            self.assertEqual(len(new_jobs),4)
            self.assertNotEqual(digest(first),digest(second))
            self.assertNotEqual(first['image_hashes'],second['image_hashes'])
            with self.assertRaisesRegex(ImageError,'single_owner_retry_spent'):
                pipeline.reject_selected(out,'cover',second['image_hashes'][0],'Again')

    async def test_subscription_images_still_need_two_owner_snapshot_decisions_and_honest_cost_card(self):
        with tempfile.TemporaryDirectory() as directory:
            c,pipeline,calls=await self.make(directory); out=Path(directory)/'out'
            document=await pipeline.run(article(),out,'run',None,subscription_enabled=True)
            c=replace(c,mode='live',contract=replace(c.contract,site_origin='https://staging.nailhub.ai',
                      api_origin='https://api-staging.nailhub.ai',media_origin=''))
            service=Service(c); service.state.set_paused(False); workflow=OnePost(service)
            saved={'article':article(),'out':str(out),'slot_at':'2026-10-27T19:00+07:00'}
            workflow.attach_images(saved,article(),document,out,None)
            with service.state.connection() as db:
                db.execute('INSERT INTO onepost_runs(run_id,binding,phase,document) VALUES(?,?,?,?)',
                           ('run','offline-binding','images_ready',json.dumps(saved)))
            before=workflow.artifact('run')
            with self.assertRaisesRegex(AdminError,'manual_image_acceptance_required'):
                await workflow.save('run','87654321-1234-1234-1234-123456789abc')
            with self.assertRaisesRegex(AdminError,'image_approval_snapshot_changed'):
                workflow.accept_images('run','stale')
            approved=workflow.accept_images('run',before['snapshot'])
            self.assertEqual(approved['phase'],'images_approved')
            with self.assertRaisesRegex(AdminError,'draft_approval_snapshot_changed'):
                workflow.approve('run',approved['snapshot'])
            store=ApprovalStore(service.state)
            image_id=store.request('images','run',before['snapshot'],100,0)
            draft_id=store.request('draft','run','draft-snapshot',100,0)
            self.assertTrue(store.decide(image_id,'ok',None,111,111,1)['ok'])
            self.assertEqual(store.get(draft_id)['status'],'pending')
            _,card,_=build_preview('images',before,[],environment='staging')
            self.assertIn('[staging]',card); self.assertIn('chi phí gói/quota chưa xác định',card)
            self.assertIn('/nailblog ok',card)
