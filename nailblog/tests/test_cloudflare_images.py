"""Offline Cloudflare Free proof, account quota and image pipeline regression fixtures."""
import base64
import copy
import io
import json
import hashlib
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext,redirect_stdout
from dataclasses import replace
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import AsyncMock,Mock,patch

from nailblog.config import CloudflareConfig,ImageConfig
from nailblog.images import ImageError
from nailblog.images.cloudflare import CloudflareImages,GENERATION,REVIEW,endpoint,image_envelope,request
from nailblog.images.neurons import NeuronLedger,utc_day
from nailblog.images.pipeline import ImagePipeline
from nailblog.images.prompts import generation_prompt as article_prompt,SCENES

def generation_prompt(brief,position):
    return article_prompt(brief,position,"nail_swatch_sticks")
from nailblog.images.post import decode
from nailblog.paid import IntentExists
from tests.helpers import load,settings
from tests.test_real_images import article,envelope,fixture_cover,fixture_image

ACCOUNT='a'*32
NOW=datetime(2026,10,19,23,59,tzinfo=timezone.utc).timestamp()


def config(directory):
    base=Path(directory)
    return settings(directory,image=ImageConfig(provider='cloudflare',model_cover=GENERATION,model_body=GENERATION,
                        review_model=REVIEW,key_file=str(base/'key.json'),candidates=1,max_rounds=1),
                    cloudflare=CloudflareConfig(ACCOUNT,str(base/'proof.json'),str(base/'quota'/'quota.sqlite3')))


def proof(c,now=NOW,**changes):
    data={'account_id':ACCOUNT,'plan':'Workers Free','observed_at':now,'expires_at':now+3600,
          'exclusive_use':True,'utc_day':utc_day(now),'baseline_neurons':208.4,
          'quota_db_path':c.cloudflare.quota_db_path}
    data.update(changes);path=Path(c.cloudflare.free_proof_file)
    path.write_text(json.dumps(data),encoding='utf-8');path.chmod(0o600)
    return data


class FixtureCloudflare(CloudflareImages):
    def __init__(self,c):super().__init__(c);self.calls=[];self.fail=None;self.review_usage=True
    def complete(self,model,body):
        self.calls.append((model,body))
        if self.fail:raise self.fail
        if model==GENERATION:
            return image_envelope({'image':base64.b64encode(fixture_image(len(self.calls),size=(1600,960))).decode()})
        scores=dict.fromkeys(('technical','anatomy','topic','brand','distinct'),True)
        value=envelope([{'text':json.dumps({'scores':scores,'reason':'fixture verdict'})}])
        value.pop('usageMetadata')
        value['_cloudflare']={'usage':{'prompt_tokens':1000,'completion_tokens':100} if self.review_usage else None}
        return value


class QuotaTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.c=config(self.tmp.name);self.now=NOW;proof(self.c)
        self.ledger=NeuronLedger(self.c,lambda:self.now);self.ledger.run('run','bind',9000)

    def test_account_identity_baseline_and_run_ceiling_are_immutable(self):
        self.ledger.attest();self.ledger.attest()
        with self.ledger.state.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM cf_baselines').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT amount FROM cf_baselines').fetchone()[0],208400)
        proof(self.c,baseline_neurons=0)
        with self.assertRaisesRegex(ImageError,'baseline_changed'):self.ledger.attest()
        with self.assertRaisesRegex(ImageError,'run_binding'):self.ledger.run('run','new',9000)
        with self.assertRaisesRegex(ImageError,'run_binding'):self.ledger.run('run','bind',8000)
        wrong=replace(self.c,cloudflare=replace(self.c.cloudflare,account_id='b'*32))
        with self.assertRaisesRegex(ImageError,'account_or_daily_cap'):NeuronLedger(wrong)

    def test_proof_must_be_fresh_free_exclusive_account_and_database_bound(self):
        changes=({'plan':'Workers Paid'},{'exclusive_use':False},{'account_id':'b'*32},
                 {'observed_at':NOW-3601},{'expires_at':NOW},{'quota_db_path':'/other/db.sqlite3'},
                 {'utc_day':'2026-10-18'})
        for change in changes:
            with self.subTest(change=change):
                proof(self.c,**change)
                with self.assertRaisesRegex(ImageError,'free_proof'):self.ledger.attest()

    def test_global_concurrent_reservations_include_baseline(self):
        barrier=threading.Barrier(2)
        def reserve(n):
            ledger=NeuronLedger(self.c,lambda:self.now);barrier.wait()
            try:ledger.reserve('call'+str(n),'cloudflare',REVIEW,5000,run_id='run');return True
            except ImageError:return False
        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(sorted(pool.map(reserve,(1,2))),[False,True])

    def test_unique_intent_unknown_and_utc_midnight_no_retry_or_reserve_reset(self):
        self.ledger.reserve('old','cloudflare',REVIEW,8500,run_id='run');self.ledger.uncertain('old')
        with self.assertRaises(IntentExists):self.ledger.reserve('old','cloudflare',REVIEW,1,run_id='run')
        self.now+=120;proof(self.c,self.now,baseline_neurons=0)
        self.assertEqual(utc_day(self.now),'2026-10-20')
        self.ledger.run('next-day','other',9000)
        with self.assertRaisesRegex(ImageError,'neuron_cap'):
            self.ledger.reserve('new','cloudflare',REVIEW,2000,run_id='next-day')
        self.assertEqual(self.ledger.get('old')['status'],'uncertain')

    def test_consumed_estimate_and_actual_usage_preserve_run_across_days(self):
        self.ledger.reserve('image','cloudflare',GENERATION,208.4,run_id='run')
        self.ledger.settle('image',1,208.4,{'estimated':True})
        self.assertEqual(self.ledger.get('image')['status'],'estimated')
        self.ledger.reserve('review','cloudflare',REVIEW,1000,run_id='run')
        self.ledger.settle('review',1,100,{'estimated':False})
        self.assertEqual(self.ledger.get('review')['status'],'settled')
        self.now+=86400;proof(self.c,self.now,baseline_neurons=0)
        with self.assertRaisesRegex(ImageError,'neuron_cap'):
            self.ledger.reserve('later','cloudflare',REVIEW,8800,run_id='run')


class AdapterTests(unittest.TestCase):
    def test_optional_reference_is_sha_pinned_downscaled_binary_and_quota_bound(self):
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'geometry.png';raw=fixture_image(17);source.write_bytes(raw)
            cfg=config(d);cfg=replace(cfg,cloudflare=replace(cfg.cloudflare,reference_image_file=str(source),
                                            reference_image_sha256=hashlib.sha256(raw).hexdigest()))
            transport=Mock(return_value={'image':base64.b64encode(fixture_image()).decode()})
            provider=CloudflareImages(cfg,key_reader=lambda *a,**kw:'fixture-key',transport=transport)
            body=provider.generation(generation_prompt('Matte versus glossy dusty rose tips',3),'1K',1536)
            binding=provider.reference_binding();reference=body['input_image_0']
            self.assertEqual(binding['source_sha256'],hashlib.sha256(raw).hexdigest())
            self.assertEqual(binding['processed_sha256'],hashlib.sha256(reference).hexdigest())
            self.assertLessEqual(max(decode(reference).size),510)
            self.assertAlmostEqual(provider.forecast(GENERATION,body),156.3+5.37)
            self.assertIn('GEOMETRY GUIDE ONLY',body['prompt'])
            self.assertIn('ring hole is in the clear handle end OPPOSITE',body['prompt'])
            self.assertIn('Do NOT copy reference object count, colors',body['prompt'])
            provider.complete(GENERATION,body)
            payload=transport.call_args.args[1]
            self.assertIn(b'name="input_image_0"; filename="geometry-reference.webp"',payload)
            self.assertIn(b'Content-Type: image/webp',payload);self.assertIn(reference,payload)
            charge,meta=provider.observed(GENERATION,{},body)
            self.assertAlmostEqual(charge,161.67);self.assertEqual(meta['input_tile_neurons'],5.37)
            self.assertEqual(meta['reference'],binding)
            changed={**body,'input_image_0':b'changed bytes'}
            with self.assertRaisesRegex(ImageError,'payload_changed'):provider.forecast(GENERATION,changed)
            # Bytes are frozen to their pinned digest for this process. A new
            # process must reject a changed source instead of silently rebinding.
            source.write_bytes(b'changed on disk')
            self.assertEqual(provider.generation(generation_prompt('brief',1),'1K',1536)['input_image_0'],reference)
            with self.assertRaisesRegex(ImageError,'reference_invalid'):CloudflareImages(cfg).reference_binding()

    def test_reference_config_symlink_bytes_bounds_and_hash_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'geometry.png';source.write_bytes(b'invalid pixels')
            cfg=config(d)
            for reference,sha in ((str(source),'0'*64),('relative.png','0'*64)):
                c=replace(cfg,cloudflare=replace(cfg.cloudflare,reference_image_file=reference,reference_image_sha256=sha))
                with self.assertRaises(ImageError):CloudflareImages(c).reference_binding()
            raw=fixture_image(18);source.write_bytes(raw)
            c=replace(cfg,cloudflare=replace(cfg.cloudflare,reference_image_file=str(source),
                                            reference_image_sha256=hashlib.sha256(raw).hexdigest()))
            with patch('pathlib.Path.is_symlink',autospec=True,side_effect=lambda path:path==source):
                with self.assertRaisesRegex(ImageError,'reference_invalid'):CloudflareImages(c).reference_binding()
            source.write_bytes(b'x'*(4*1024*1024+1))
            with self.assertRaisesRegex(ImageError,'reference_invalid'):CloudflareImages(c).reference_binding()
            for body in ('reference_image_file="/tmp/reference.png"',
                         'reference_image_sha256="'+'0'*64+'"',
                         'reference_image_file="/tmp/reference.png"\nreference_image_sha256="bad"'):
                with self.assertRaises(ValueError):load(d,cloudflare=body)

    def test_reference_review_rejects_double_ended_or_painted_handles(self):
        with tempfile.TemporaryDirectory() as d:
            provider=CloudflareImages(config(d))
            instruction=provider.review(b'final-webp',generation_prompt('nail sticks',1),1024)['messages'][0]['content'][0]['text']
            self.assertIn('painted/colored tips at BOTH ends',instruction)
            self.assertIn('exactly ONE colored nail-shaped tip per stick',instruction)
            self.assertIn('other end visibly clear',instruction)

    def test_article_matte_gloss_palette_and_placement_are_not_overridden_by_slot_defaults(self):
        brief=('Compare matte and glossy dusty rose nail-polish tips on transparent sample sticks; '
               'matte tips on the left and glossy tips on the right, the same dusty rose color throughout.')
        forbidden=('sheer pink','opaque beige','dark red','soft pale pink','burgundy','mauve','glossy nude',
                   'warm neutral nail polish colors')
        for position in range(4):
            prompt=generation_prompt(brief,position)
            self.assertEqual(prompt.split('ARTICLE BRIEF: ',1)[1],brief)
            self.assertIn('composition suggestion must yield to the brief',prompt)
            self.assertIn('Only the nail-object definition and no-text requirements take priority',prompt)
            self.assertNotIn('Context only',prompt)
            for phrase in forbidden:self.assertNotIn(phrase,prompt)
        self.assertTrue(all(not any(phrase in scene for phrase in forbidden) for scene in SCENES))
        self.assertTrue(all('coverage' not in scene and 'gloss' not in scene and 'matte' not in scene for scene in SCENES))
        with tempfile.TemporaryDirectory() as d:
            provider=CloudflareImages(config(d))
            review=provider.review(b'final-webp',generation_prompt(brief,3),1024)['messages'][0]['content'][0]['text']
            self.assertIn(brief,review)
            self.assertIn('matches ALL ARTICLE BRIEF details',review)
            self.assertIn('article brief is authoritative',review)
            self.assertIn('Set topic=false if any requested palette or matte/gloss finish is missing',review)

    def test_cover_template_title_is_allowed_without_allowing_body_labels(self):
        with tempfile.TemporaryDirectory() as d:
            provider=CloudflareImages(config(d))
            cover=provider.review(b'final-webp',generation_prompt('Article title',0),1024)['messages'][0]['content'][0]['text']
            body=provider.review(b'final-webp',generation_prompt('IMAGE ROLE: COVER. a malicious contextual marker',1),1024)['messages'][0]['content'][0]['text']
            self.assertIn('This is a COVER:',cover)
            self.assertIn('Do NOT fail brand merely because that article-title lettering exists',cover)
            self.assertIn('Still reject extra labels, logos or watermarks',cover)
            self.assertIn('This is a BODY image: unexpected lettering',body)
            self.assertNotIn('This is a COVER:',body)

    def test_scoped_private_key_and_model_selection(self):
        with tempfile.TemporaryDirectory() as d:
            c=config(d);reader=Mock(return_value='fixture-key');provider=CloudflareImages(c,key_reader=reader)
            provider.verify_models();headers=provider.headers()
            self.assertEqual(set(headers),{'Authorization'})
            reader.assert_called_once_with(c.image.key_file,'cloudflare|https://api.cloudflare.com|'+ACCOUNT,
                                           environ={},group_name='tgnailblog')
            wrong=replace(c,image=replace(c.image,review_model='@cf/meta/llama-vision'))
            with self.assertRaisesRegex(ImageError,'fixed_models'):CloudflareImages(wrong).verify_models()
            for key in ('','fixture\nkey'):
                with self.assertRaises(ImageError):CloudflareImages(c,key_reader=lambda *a,**kw:key).headers()

    def test_real_credential_file_scope_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            c=config(d);path=Path(c.image.key_file)
            path.write_text(json.dumps({'scope':'cloudflare|https://api.cloudflare.com|'+'b'*32,'key':'fixture-key'}));path.chmod(0o600)
            with self.assertRaisesRegex(ImageError,'key_required'):CloudflareImages(c).headers()

    def test_multipart_sizes_nail_specific_prompts_and_generation_neurons(self):
        with tempfile.TemporaryDirectory() as d:
            provider=CloudflareImages(config(d),key_reader=lambda *a,**kw:'fixture-key')
            payloads=[]
            def transport(url,body,headers,timeout):
                payloads.append((url,body,headers));return {'image':base64.b64encode(fixture_image()).decode()}
            provider.transport=transport
            for size,dimensions,neurons in (('2K',(1600,960),208.4),('1K',(1280,720),156.3)):
                body=provider.generation(generation_prompt('infographic arrows and letters',0),size,2048)
                self.assertEqual((body['width'],body['height']),dimensions)
                self.assertAlmostEqual(provider.forecast(GENERATION,body),neurons)
                answer=provider.complete(GENERATION,body)
                self.assertNotIn('usageMetadata',answer)
            self.assertIn(b'Content-Disposition: form-data; name="width"',payloads[0][1])
            self.assertIn('multipart/form-data',payloads[0][2]['Content-Type'])
            prompts=[generation_prompt('context',n) for n in range(4)]
            self.assertEqual(len(set(prompts)),4)
            for prompt in prompts:
                self.assertIn('FLAT TRANSPARENT PLASTIC',prompt);self.assertIn('FINGERNAIL-SHAPED',prompt)
                self.assertIn('NEVER draw lipstick',prompt)

    def test_final_webp_review_thinking_disabled_output_bounded_and_usage_not_double_counted(self):
        with tempfile.TemporaryDirectory() as d:
            provider=CloudflareImages(config(d));body=provider.review(b'final-webp','brief',1024)
            self.assertEqual(body['chat_template_kwargs'],{'enable_thinking':False})
            self.assertEqual((body['n'],body['max_completion_tokens'],body['stream']),(1,1024,False))
            self.assertEqual(base64.b64decode(body['messages'][0]['content'][1]['image_url']['url'].split(',')[1]),b'final-webp')
            self.assertNotIn('cylindrical cosmetic tubes',body['messages'][0]['content'][0]['text'])
            answer={'_cloudflare':{'usage':{'prompt_tokens':1000,'completion_tokens':100,
                                          'completion_tokens_details':{'reasoning_tokens':50}}}}
            charge,meta=provider.observed(REVIEW,answer,body)
            self.assertAlmostEqual(charge,(1000*40909+100*290909)/1e6)
            self.assertFalse(meta['estimated'])
            for usage in (None,{}, {'prompt_tokens':32769,'completion_tokens':100}):
                with self.assertRaisesRegex(ImageError,'usage_unknown'):provider.observed(REVIEW,{'_cloudflare':{'usage':usage}},body)

    def test_endpoint_redirect_errors_oversized_body_and_private_dns_fail_closed(self):
        valid=endpoint(ACCOUNT,GENERATION)
        for url in (valid+'?key=bad',valid.replace('api.cloudflare.com','api.cloudflare.com.attacker.test'),
                    valid.replace('/ai/run/','/ai/other/'),valid.replace('@cf/black-forest-labs/flux-2-klein-4b','@cf/other/model')):
            with self.assertRaisesRegex(ImageError,'endpoint'):request(url,b'body',{},1)
        with patch('nailblog.images.cloudflare.resolve') as resolver:
            with self.assertRaisesRegex(ImageError,'request_too_large'):request(valid,b'x'*(2*1024*1024+1),{},1)
            resolver.assert_not_called()
        with patch('nailblog.images.cloudflare.resolve',return_value=[(None,None,None,None,('127.0.0.1',443))]):
            with self.assertRaisesRegex(ImageError,'endpoint'):request(valid,b'body',{},1)
        for status,code in ((302,'provider_failed'),(429,'rate_limited'),(403,'permission_denied')):
            connection=Mock();connection.getresponse.return_value=Mock(status=status)
            with patch('nailblog.images.cloudflare.resolve',return_value=[(None,None,None,None,('8.8.8.8',443))]), \
                    patch('nailblog.images.cloudflare.PinnedHTTPS',return_value=connection):
                with self.assertRaisesRegex(ImageError,code):request(valid,b'body',{},1)
            connection.request.assert_called_once()

    def test_invalid_image_and_review_response_never_pass(self):
        for data in ({},{'image':'!!!!'},{'image':'https://other.test/picture.png'}):
            with self.assertRaises(ImageError):image_envelope(data)
        with tempfile.TemporaryDirectory() as d:
            c=config(d)
            for result in ({'response':'unsupported legacy shape'}, {'choices':[{'finish_reason':'length'}]}):
                provider=CloudflareImages(c,key_reader=lambda *a,**kw:'fixture-key',transport=Mock(return_value=result))
                with self.assertRaises(ImageError):provider.complete(REVIEW,provider.review(b'bytes','brief',1024))

    def test_config_cap_and_account_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            for body in ('account_id="wrong"','daily_neurons=10001','quota_db_path="relative.sqlite3"'):
                with self.assertRaises(ValueError):load(d,cloudflare=body)
            self.assertEqual(load(d,image='provider="cloudflare"').image.provider,'cloudflare')


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.c=config(self.tmp.name)
        self.now=NOW;proof(self.c);self.provider=FixtureCloudflare(self.c);self.out=Path(self.tmp.name)/'output'
        self.pipeline=ImagePipeline(self.c,provider=self.provider,clock=lambda:self.now,cover_renderer=fixture_cover)
        patcher=patch('nailblog.images.pipeline.MODEL_LOCK.enter',return_value=nullcontext());patcher.start();self.addCleanup(patcher.stop)

    async def run_pipeline(self):return await self.pipeline.run(article(),self.out,'free-run',9000,free_enabled=True)

    async def test_operator_rejection_audits_veto_keeps_good_bodies_and_resumes_next_untried_candidate(self):
        self.c=replace(self.c,image=replace(self.c.image,candidates=2))
        self.provider=FixtureCloudflare(self.c)
        self.pipeline=ImagePipeline(self.c,provider=self.provider,clock=lambda:self.now,cover_renderer=fixture_cover)
        document=await self.run_pipeline();manifest_path=self.out/'manifest.json'
        before=json.loads(manifest_path.read_text(encoding='utf-8'));original_bytes=manifest_path.read_bytes()
        sha=document['image_hashes'][0];operations=copy.deepcopy(self.pipeline.ledger.report('free-run'))
        with self.assertRaisesRegex(ImageError,'selected_sha_mismatch'):
            self.pipeline.reject_selected(self.out,'cover','0'*64,'Unexpected logo')
        self.assertEqual(manifest_path.read_bytes(),original_bytes)
        from nailblog._runtime import ProcessLock,AlreadyRunning
        with ProcessLock(self.out):
            with self.assertRaises(AlreadyRunning):self.pipeline.reject_selected(self.out,'cover',sha,'Unexpected logo')
        damaged={**before,'binding':'not the ledger binding'}
        manifest_path.write_text(json.dumps(damaged),encoding='utf-8')
        with self.assertRaisesRegex(ImageError,'run_binding_mismatch'):
            self.pipeline.reject_selected(self.out,'cover',sha,'Unexpected logo')
        manifest_path.write_bytes(original_bytes)
        event=self.pipeline.reject_selected(self.out,'cover',sha,'Unexpected gibberish logo outside intentional title')
        rejected=json.loads(manifest_path.read_text(encoding='utf-8'))
        self.assertEqual((rejected['status'],rejected['artifacts_stale']),('held',True))
        self.assertNotIn('cover',rejected['positions']);self.assertNotIn('document_digest',rejected)
        self.assertEqual(rejected['candidates']['cover-r0-c0']['status'],'operator_rejected')
        self.assertEqual(rejected['candidates']['cover-r0-c0']['scores'],before['candidates']['cover-r0-c0']['scores'])
        self.assertEqual(rejected['candidates']['cover-r0-c0']['reason'],before['candidates']['cover-r0-c0']['reason'])
        self.assertEqual(event['model_scores'],before['positions']['cover']['scores'])
        self.assertTrue(all((self.out/name).exists() for name in event['stale_artifacts']))
        self.assertTrue(Path(document['cover_path']).exists())
        self.assertEqual(self.pipeline.ledger.report('free-run'),operations)
        revised=await self.run_pipeline()
        self.assertEqual(len(self.provider.calls),10)  # Original 8 + only replacement cover generate/review.
        self.assertEqual(revised['image_paths'],document['image_paths'])
        self.assertNotEqual(revised['cover_path'],document['cover_path'])
        final=json.loads(manifest_path.read_text(encoding='utf-8'))
        self.assertEqual(final['positions']['cover']['file'],Path(revised['cover_path']).name)
        self.assertEqual(final['candidates']['cover-r0-c0']['status'],'operator_rejected')
        self.assertFalse(final['artifacts_stale']);self.assertEqual(len(final['operator_rejections']),1)
        # An unknown request in the same run blocks further operator mutation.
        self.pipeline.ledger.reserve('unknown','cloudflare',REVIEW,10,run_id='free-run')
        self.pipeline.ledger.uncertain('unknown');unchanged=manifest_path.read_bytes()
        with self.assertRaisesRegex(ImageError,'requires_reconciliation'):
            self.pipeline.reject_selected(self.out,'body-1',revised['image_hashes'][1],'Operator veto')
        self.assertEqual(manifest_path.read_bytes(),unchanged)

    async def test_full_cover_three_bodies_admin_contract_and_complete_resume_no_network(self):
        document=await self.run_pipeline()
        self.assertEqual(len(document['image_hashes']),4);self.assertEqual(len(self.provider.calls),8)
        rows=self.pipeline.ledger.report('free-run')
        self.assertEqual([row['status'] for row in rows].count('estimated'),4)
        self.assertEqual([row['status'] for row in rows].count('settled'),4)
        self.now+=7200  # Complete cache is offline even after proof expires.
        self.assertEqual(await self.run_pipeline(),document);self.assertEqual(len(self.provider.calls),8)

    async def test_no_explicit_free_or_expired_proof_prevents_generation(self):
        with self.assertRaisesRegex(ImageError,'free_enable'):await self.pipeline.run(article(),self.out,'free-run',9000)
        proof(self.c,expires_at=NOW)
        with self.assertRaisesRegex(ImageError,'free_proof'):await self.run_pipeline()
        self.assertEqual(self.provider.calls,[])

    async def test_proof_revalidated_inside_lock_after_wait(self):
        pipeline=self.pipeline
        class WaitedLock:
            def __enter__(self):proof(pipeline.config,expires_at=NOW)
            def __exit__(self,*args):pass
        with patch('nailblog.images.pipeline.MODEL_LOCK.enter',return_value=WaitedLock()):
            with self.assertRaisesRegex(ImageError,'free_proof'):await self.run_pipeline()
        self.assertEqual(self.provider.calls,[])
        self.assertEqual(self.pipeline.ledger.report('free-run')[0]['status'],'uncertain')

    async def test_429_is_uncertain_no_network_retry_on_restart(self):
        self.provider.fail=ImageError('cloudflare_rate_limited')
        with self.assertRaisesRegex(ImageError,'rate_limited'):await self.run_pipeline()
        self.assertEqual(len(self.provider.calls),1)
        self.assertEqual(self.pipeline.ledger.report('free-run')[0]['status'],'uncertain')
        self.provider.fail=None
        with self.assertRaisesRegex(ImageError,'reconciliation'):await self.run_pipeline()
        self.assertEqual(len(self.provider.calls),1)

    async def test_missing_review_usage_keeps_neurons_reserved_and_holds(self):
        self.provider.review_usage=False
        with self.assertRaisesRegex(ImageError,'usage_unknown'):await self.run_pipeline()
        self.assertEqual([row['status'] for row in self.pipeline.ledger.report('free-run')],['estimated','uncertain'])
        self.assertFalse((self.out/'document.json').exists())


class CliTests(unittest.TestCase):
    def test_free_cli_optin_and_budget_are_provider_explicit(self):
        from nailblog.__main__ import main
        with tempfile.TemporaryDirectory() as d:
            c=config(d);source=Path(d)/'article.json';source.write_text(json.dumps(article()))
            args=['images','--article',str(source),'--out',str(Path(d)/'out'),'--run-id','free-run']
            for flags in ([],['--free-enabled'],['--free-enabled','--run-neurons','9000','--paid-enabled','--run-usd','1']):
                with patch('sys.stderr',new=io.StringIO()),self.assertRaises(SystemExit):main(args+flags)
            with patch('nailblog.__main__.load_config',return_value=c), \
                    patch('nailblog.images.pipeline.ImagePipeline.run',new=AsyncMock(return_value={'image_hashes':['a']*4})) as run, \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(main(args+['--free-enabled','--run-neurons','9000']),0)
            self.assertEqual(run.await_args.kwargs,{'paid_enabled':False,'free_enabled':True})


if __name__=='__main__':unittest.main()
