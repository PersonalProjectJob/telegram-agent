"""Offline image, paid-intent and recovery tests. All generated pixels/providers are fixtures."""
import asyncio
import base64
import copy
import io
import json
import os
import random
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import datetime,timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock,patch

from PIL import Image,ImageDraw

from nailblog.config import BudgetConfig,ImageConfig
from nailblog.images import ImageError
from nailblog.images.pipeline import ImagePipeline,artifact,slots
from nailblog.images.post import crop,decode,hashes,webp
from nailblog.images.provider import GeminiImages,image_of,parts_of,request,review_of,usage
from nailblog.paid import IntentExists,PaidLedger
from nailblog.schedule import VN_TZ
from nailblog.service import Service
from nailblog.state import State
from nailblog.usage import BudgetExceeded,UsageRecorder
from tests.helpers import settings,load


def fixture_image(seed=1,size=(1700,1000)):
    rng=random.Random(seed);small=Image.new('RGB',(18,12));draw=ImageDraw.Draw(small)
    for y in range(12):
        for x in range(18):draw.point((x,y),fill=tuple(rng.randrange(256) for _ in range(3)))
    image=small.resize(size,Image.Resampling.BICUBIC);stream=io.BytesIO();image.save(stream,format='PNG')
    return stream.getvalue()


def envelope(parts,*,finish='STOP',meta=None):
    return {'candidates':[{'finishReason':finish,'content':{'parts':parts}}],
            'usageMetadata':meta if meta is not None else {'promptTokenCount':100,'candidatesTokenCount':100,'thoughtsTokenCount':0}}


def article():
    return {'title':'Bảng màu nail dễ chọn cho khách','slug':'bang-mau-nail','summary':'Tóm tắt',
            'sections':[{'h2':f'Mục {n}','paragraphs':['Nội dung mẫu.'],
                         'image_slot':{'brief':f'Nail color swatches distinct palette {n}',
                                       'alt':'Bảng màu móng tay minh họa tư vấn khách','caption':'Màu minh họa'}} for n in range(3)]}


def config(directory,**image):
    return settings(directory,image=ImageConfig(model_body='image-test',model_cover='image-test',review_model='review-test',
                    candidates=1,max_rounds=1,**image),
                    budget=BudgetConfig(1.0,{'image-test':(.5,3),'review-test':(.3,2.5)}, {},{'image-test':60}))


class FixtureProvider(GeminiImages):
    def __init__(self,c):super().__init__(c);self.calls=[];self.checked=0;self.fail=None;self.verdict=True
    def verify_models(self):self.checked+=1
    def count(self,*args):return 100
    def complete(self,model,body):
        self.calls.append((model,body))
        if self.fail:raise self.fail
        if model=='image-test':
            data=fixture_image(len(self.calls))
            return envelope([{'inlineData':{'mimeType':'image/png','data':base64.b64encode(data).decode()}}])
        return envelope([{'text':json.dumps({'scores':dict.fromkeys(('technical','anatomy','topic','brand','distinct'),self.verdict),
                                             'reason':'fixture review'})}])


async def fixture_cover(image,title):return image


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.c=config(self.temp.name);self.s=State(self.c.db_path)
        self.now=datetime(2026,10,19,23,59,tzinfo=VN_TZ).timestamp()
        self.l=PaidLedger(self.s,self.c,lambda:self.now);self.l.run('run','binding',1)

    def test_atomic_concurrent_reservations_only_one_winner(self):
        barrier=threading.Barrier(2)
        def reserve(n):
            ledger=PaidLedger(State(self.c.db_path),self.c,lambda:self.now);barrier.wait()
            try:ledger.reserve('i'+str(n),'gemini','image-test',.6,run_id='run');return True
            except BudgetExceeded:return False
        with ThreadPoolExecutor(2) as pool:self.assertEqual(sorted(pool.map(reserve,(1,2))),[False,True])

    def test_unique_intent_cannot_be_claimed_again(self):
        self.l.reserve('same','gemini','image-test',.1,run_id='run')
        with self.assertRaises(IntentExists):self.l.reserve('same','gemini','image-test',.1,run_id='run')

    def test_unknown_reservation_survives_midnight_and_restart(self):
        self.l.reserve('unknown','gemini','image-test',.8,run_id='run');self.l.uncertain('unknown')
        self.now+=120
        new=PaidLedger(State(self.c.db_path),self.c,lambda:self.now)
        with self.assertRaises(BudgetExceeded):new.reserve('new','gemini','image-test',.3,run_id='run')
        self.assertEqual(new.get('unknown')['status'],'uncertain')

    def test_run_spend_never_resets_with_daily_boundary(self):
        self.l.reserve('paid','gemini','image-test',.8,run_id='run');self.l.settle('paid',10,.8)
        self.now+=86400
        with self.assertRaises(BudgetExceeded):self.l.reserve('next','gemini','image-test',.3,run_id='run')
        self.assertEqual(len(self.s.usage_between(0,self.now+1)),1)

    def test_run_identity_price_cap_and_article_binding_are_immutable(self):
        for binding,cap in (('different',1),('binding',2)):
            with self.assertRaises(ValueError):self.l.run('run',binding,cap)

    def test_settlement_is_atomic_and_cannot_exceed_reservation(self):
        self.l.reserve('one','gemini','image-test',.2,run_id='run')
        with self.assertRaises(ValueError):self.l.settle('one',1,.3)
        self.assertEqual(self.s.usage_between(0,self.now+1),[])
        self.l.settle('one',1,.15)
        with self.assertRaises(ValueError):self.l.settle('one',1,.15)
        self.assertEqual(len(self.s.usage_between(0,self.now+1)),1)

    def test_usage_check_sees_active_image_reservations(self):
        self.l.reserve('all','gemini','image-test',1,run_id='run')
        with self.assertRaises(BudgetExceeded):UsageRecorder(self.s,self.c,clock=lambda:self.now).check()


class PostTests(unittest.TestCase):
    def test_cropped_webp_dimensions_size_hash_and_rejection_of_upscale(self):
        data=fixture_image();image=crop(data,(1600,900));encoded=webp(image,200*1024)
        self.assertEqual(decode(encoded).size,(1600,900));self.assertLessEqual(len(encoded),200*1024)
        self.assertEqual(hashes(encoded),hashes(encoded))
        with self.assertRaisesRegex(ImageError,'resolution'):crop(fixture_image(size=(100,100)),(1600,900))

    def test_invalid_bytes_and_oversized_dimensions_rejected_before_loading(self):
        for data in (b'not an image',b'',b'x'*(16*1024*1024+1)):
            with self.assertRaises(ImageError):decode(data)
        image=Mock(format='PNG',n_frames=1,size=(10000,10000),width=10000,height=10000)
        with patch('nailblog.images.post.Image.open',return_value=image):
            with self.assertRaises(ImageError):decode(b'fake')
        image.load.assert_not_called()

    def test_alt_and_slot_count_preflight(self):
        self.assertEqual(len(slots(article())),4)
        bad=article();bad['sections'][0]['image_slot']['alt']='ngắn'
        with self.assertRaises(ImageError):slots(bad)
        with self.assertRaises(ImageError):slots({**article(),'sections':[]})

    def test_artifact_path_cannot_escape_output_directory(self):
        for name in ('../private.webp','/private.webp','a/b.webp','x\\a.webp','a.webp:secret'):
            with self.assertRaises(ImageError):artifact(Path('.'),name)

    def test_json_writer_rejects_destination_or_temporary_symlink_before_write(self):
        from nailblog.images.pipeline import write_atomic
        with tempfile.TemporaryDirectory() as d:
            target=Path(d)/'document.json'
            for suffix in ('.json','.tmp'):
                with patch('pathlib.Path.is_symlink',autospec=True,side_effect=lambda path:path.suffix==suffix), \
                        patch('nailblog.images.pipeline._write_atomic') as writer:
                    with self.assertRaisesRegex(ImageError,'symlink'):write_atomic(target,{'x':1})
                    writer.assert_not_called()


class ProviderTests(unittest.TestCase):
    def test_http_errors_oversize_private_dns_and_body_bound_never_retry(self):
        url='https://generativelanguage.googleapis.com/v1beta/models/a:generateContent'
        for status,code in ((429,'rate_limited'),(403,'permission_denied'),(500,'provider_failed')):
            response=Mock(status=status);connection=Mock();connection.getresponse.return_value=response
            with patch('nailblog.images.provider.resolve',return_value=[(None,None,None,None,('8.8.8.8',443))]), \
                    patch('nailblog.images.provider.PinnedHTTPS',return_value=connection):
                with self.assertRaisesRegex(ImageError,code):request(url,{}, {},10)
            connection.request.assert_called_once()
        with patch('nailblog.images.provider.resolve') as resolver:
            with self.assertRaisesRegex(ImageError,'request_too_large'):request(url,{'x':'x'*(2*1024*1024)}, {},10)
            resolver.assert_not_called()
        with patch('nailblog.images.provider.resolve',return_value=[(None,None,None,None,('127.0.0.1',443))]), \
                patch('nailblog.images.provider.PinnedHTTPS') as connection:
            with self.assertRaisesRegex(ImageError,'invalid_endpoint'):request(url,{}, {},10)
            connection.assert_not_called()
        response=Mock(status=200);response.read1.side_effect=[b'12345'];connection=Mock();connection.getresponse.return_value=response
        with patch('nailblog.images.provider.resolve',return_value=[(None,None,None,None,('8.8.8.8',443))]), \
                patch('nailblog.images.provider.PinnedHTTPS',return_value=connection), \
                patch('nailblog.images.provider.MAX_RESPONSE',4):
            with self.assertRaisesRegex(ImageError,'response_too_large'):request(url,{}, {},10)

    def test_models_scope_and_count_tokens_are_validated_without_generation(self):
        with tempfile.TemporaryDirectory() as d:
            reader=Mock(return_value='fixture-key')
            transport=Mock(return_value={'models':[{'name':'models/image-test','supportedGenerationMethods':['generateContent']},
                                                  {'name':'models/review-test','supportedGenerationMethods':['generateContent']}]})
            provider=GeminiImages(config(d),transport=transport,key_reader=reader);provider.verify_models()
            reader.assert_called_with('', 'gemini|https://generativelanguage.googleapis.com',group_name='tgnailblog')
            transport.return_value={'totalTokens':5000}
            with self.assertRaisesRegex(ImageError,'input_ceiling'):provider.count('image-test',{'contents':[]},4096)
            transport.return_value={'models':[]}
            with self.assertRaisesRegex(ImageError,'model_not_available'):provider.verify_models()

    def test_generation_contract_and_review_final_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            provider=GeminiImages(config(d));body=provider.generation('prompt','2K',2048)
            cfg=body['generationConfig']
            self.assertEqual(cfg['imageConfig'],{'aspectRatio':'16:9','imageSize':'2K'})
            self.assertEqual((cfg['candidateCount'],cfg['maxOutputTokens']),(1,2048));self.assertNotIn('tools',body)
            final=b'final processed bytes';review=provider.review(final,'brief',1024)
            self.assertEqual(base64.b64decode(review['contents'][0]['parts'][1]['inlineData']['data']),final)

    def test_refusal_truncation_unknown_usage_and_multiple_images_fail(self):
        for finish in ('MAX_TOKENS','SAFETY','OTHER'):
            with self.assertRaises(ImageError):parts_of(envelope([],finish=finish))
        for meta in ({'promptTokenCount':100},{'candidatesTokenCount':100},{'promptTokenCount':True,'candidatesTokenCount':1}):
            with self.assertRaises(ImageError):usage(envelope([],meta=meta))
        for parts in ([],[{'inlineData':{'mimeType':'image/png','data':'!!!!'}}],
                      [{'fileData':{'fileUri':'https://attacker.test/a.png'}}]):
            with self.assertRaises(ImageError):image_of(envelope(parts))

    def test_review_missing_or_nonboolean_rubric_is_never_pass(self):
        for data in ({'scores':{}}, {'scores':dict.fromkeys(('technical','anatomy','topic','brand','distinct'),1),'reason':'x'}):
            with self.assertRaises(ImageError):review_of(envelope([{'text':json.dumps(data)}]))

    def test_origin_path_credentials_and_redirect_rejected(self):
        for url in ('https://example.test/v1beta/models/a:generateContent',
                    'https://generativelanguage.googleapis.com/v1beta/models/a:generateContent?key=secret',
                    'https://user@generativelanguage.googleapis.com/v1beta/models/a:generateContent',
                    'http://generativelanguage.googleapis.com/v1beta/models/a:generateContent'):
            with self.assertRaisesRegex(ImageError,'endpoint'):request(url,{}, {},10)
        response=Mock(status=302);connection=Mock();connection.getresponse.return_value=response
        with patch('nailblog.images.provider.resolve',return_value=[(None,None,None,None,('8.8.8.8',443))]), \
                patch('nailblog.images.provider.PinnedHTTPS',return_value=connection):
            with self.assertRaisesRegex(ImageError,'provider_failed'):
                request('https://generativelanguage.googleapis.com/v1beta/models/a:generateContent',{}, {},10)
        connection.request.assert_called_once();response.close.assert_called_once()


class ImageCliTests(unittest.TestCase):
    def test_rate_limit_code_visible_without_raw_provider_or_key_data(self):
        from nailblog.__main__ import main
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'article.json';source.write_text(json.dumps(article()),encoding='utf-8')
            output=io.StringIO()
            # Only the sanitized operation code crosses the CLI boundary; raw
            # transport/credential details are never part of ImageError.
            with patch('nailblog.__main__.load_config',return_value=config(d)), \
                    patch('nailblog.images.pipeline.ImagePipeline.run',new=AsyncMock(side_effect=ImageError('rate_limited'))), \
                    redirect_stdout(output):
                result=main(['images','--article',str(source),'--out',str(Path(d)/'out'),
                             '--run-id','stable-run','--run-usd','1','--paid-enabled'])
            self.assertEqual(result,1)
            self.assertEqual(output.getvalue().strip(),'NailBlog operation failed: rate_limited')
            self.assertNotIn('api-key',output.getvalue());self.assertNotIn('fixture-key',output.getvalue())

    def test_unexpected_provider_exception_does_not_print_credential_details(self):
        from nailblog.__main__ import main
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'article.json';source.write_text(json.dumps(article()),encoding='utf-8')
            output=io.StringIO()
            with patch('nailblog.__main__.load_config',return_value=config(d)), \
                    patch('nailblog.images.pipeline.ImagePipeline.run',new=AsyncMock(
                        side_effect=OSError('upstream x-goog-api-key=fixture-key'))), redirect_stdout(output):
                result=main(['images','--article',str(source),'--out',str(Path(d)/'out'),
                             '--run-id','stable-run','--run-usd','1','--paid-enabled'])
            self.assertEqual(result,1)
            self.assertEqual(output.getvalue().strip(),'NailBlog operation failed: OSError')
            self.assertNotIn('fixture-key',output.getvalue())


class PipelineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.c=config(self.temp.name)
        self.provider=FixtureProvider(self.c);self.out=Path(self.temp.name)/'output'
        self.pipeline=ImagePipeline(self.c,provider=self.provider,cover_renderer=fixture_cover)
        self.lock=patch('nailblog.images.pipeline.MODEL_LOCK.enter',return_value=nullcontext());self.lock.start();self.addCleanup(self.lock.stop)

    async def run_pipeline(self,**kw):
        return await self.pipeline.run(article(),self.out,'run',1,paid_enabled=True,**kw)

    async def test_review_bypass_switch_enters_the_run_binding_only_when_on(self):
        from dataclasses import asdict
        from importlib.resources import files
        from nailblog.images.post import VERSION
        from nailblog.images.prompts import VERSION as PROMPT_VERSION,REVIEW_VERSION
        from nailblog.publication import digest
        def expected(c,directory,*,keep):
            params=asdict(c.image);params.pop('key_file')
            if not keep:params.pop('review_bypass')
            if params['key_source']=='file':params.pop('key_source')
            prices=asdict(c.budget)
            if not prices['image_unit_prices']:prices.pop('image_unit_prices')
            return digest({'article':article(),'image':params,'prices':prices,'processing':VERSION,
                           'review':REVIEW_VERSION,'directory':str(Path(directory).absolute()),
                           'template':digest(files('nailblog.images').joinpath('templates/cover.html').read_text(encoding='utf-8')),
                           'prompt_version':PROMPT_VERSION,'cloudflare':None})
        await self.run_pipeline()
        off=json.loads((self.out/'manifest.json').read_text(encoding='utf-8'))['binding']
        # Pre-switch behaviour: the digest is computed WITHOUT a review_bypass key.
        self.assertEqual(off,expected(self.c,self.out,keep=False))
        self.assertNotEqual(off,expected(self.c,self.out,keep=True))
        on_config=replace(self.c,image=replace(self.c.image,review_bypass=True));on_out=Path(self.temp.name)/'output-on'
        on_provider=FixtureProvider(on_config);on_provider.calls=[None]*50   # distinct pixels from the first run
        await ImagePipeline(on_config,provider=on_provider,cover_renderer=fixture_cover).run(
            article(),on_out,'run-on',1,paid_enabled=True)
        on=json.loads((on_out/'manifest.json').read_text(encoding='utf-8'))['binding']
        self.assertEqual(on,expected(on_config,on_out,keep=True))

    async def test_complete_outputs_exact_admin_contract_and_resume_never_calls_provider(self):
        document=await self.run_pipeline()
        self.assertEqual(len(document['image_hashes']),4);self.assertEqual(len(self.provider.calls),8)
        self.assertEqual(set(document['image_reviews']),set(document['image_hashes']))
        self.assertTrue(all(all(scores.values()) for scores in document['image_reviews'].values()))
        self.assertTrue((self.out/'preview.html').exists())
        self.provider.complete=Mock(side_effect=AssertionError('duplicate paid call'))
        self.assertEqual(await self.run_pipeline(),document)
        self.provider.complete.assert_not_called()

    async def test_explicit_paid_enable_required(self):
        with self.assertRaisesRegex(ImageError,'paid_enable'):
            await self.pipeline.run(article(),self.out,'run',1)
        self.assertEqual(self.provider.calls,[])

    async def test_small_run_budget_and_count_bound_stop_before_generation(self):
        with self.assertRaises(BudgetExceeded):
            await self.pipeline.run(article(),self.out,'run',.001,paid_enabled=True)
        self.assertEqual(self.provider.calls,[])
        self.assertEqual(self.pipeline.ledger.report('run'),[])

    async def test_output_lock_prevents_a_second_pipeline(self):
        from nailblog._runtime import ProcessLock,AlreadyRunning
        self.out.mkdir()
        with ProcessLock(self.out):
            with self.assertRaises(AlreadyRunning):await self.run_pipeline()
        self.assertEqual(self.provider.calls,[])

    async def test_historical_duplicate_never_reaches_model_review(self):
        from nailblog.images.post import crop,webp,hashes
        data=webp(crop(fixture_image(1),(1600,900)),200*1024);sha,dh=hashes(data)
        self.pipeline.historical_hashes=Mock(return_value=[dh])
        with self.assertRaisesRegex(ImageError,'quality_held'):await self.run_pipeline()
        self.assertEqual(len(self.provider.calls),1)

    async def test_non_image_generation_or_missing_usage_cannot_produce_document(self):
        self.provider.complete=Mock(return_value=envelope([{'text':'not an image'}],meta={'promptTokenCount':100}))
        with self.assertRaisesRegex(ImageError,'usage_unknown'):await self.run_pipeline()
        self.assertFalse((self.out/'document.json').exists())
        self.assertEqual(self.pipeline.ledger.report('run')[0]['status'],'uncertain')

    async def test_timeout_keeps_reservation_and_restart_never_generates_again(self):
        self.provider.fail=TimeoutError('not logged')
        with self.assertRaises(TimeoutError):await self.run_pipeline()
        self.assertEqual(len(self.provider.calls),1)
        intents=self.pipeline.ledger.report('run');self.assertEqual(intents[0]['status'],'uncertain')
        self.assertGreater(intents[0]['reserved'],0)
        self.provider.fail=None
        with self.assertRaisesRegex(ImageError,'candidate_requires_reconciliation'):await self.run_pipeline()
        self.assertEqual(len(self.provider.calls),1)

    async def test_missing_usage_or_truncated_response_retains_full_reservation(self):
        self.provider.complete=Mock(return_value=envelope([],finish='MAX_TOKENS'))
        with self.assertRaisesRegex(ImageError,'truncated'):await self.run_pipeline()
        intent=self.pipeline.ledger.report('run')[0]
        self.assertEqual((intent['status'],intent['charged']),('uncertain',0))

    async def test_reviewer_failure_holds_instead_of_inventing_pass(self):
        self.provider.verdict=False
        with self.assertRaisesRegex(ImageError,'quality_held'):await self.run_pipeline()
        self.assertFalse((self.out/'document.json').exists());self.assertEqual(len(self.provider.calls),2)

    async def test_changed_article_config_and_tampered_file_do_not_generate_again(self):
        document=await self.run_pipeline();calls=len(self.provider.calls)
        with self.assertRaisesRegex(ValueError,'binding'):
            await self.pipeline.run({**article(),'title':'Different article title'},self.out,'run',1,paid_enabled=True)
        changed={**document,'title':'Changed after completion','content_html':'<p>Tampered content</p>'}
        (self.out/'document.json').write_text(json.dumps(changed),encoding='utf-8')
        with self.assertRaisesRegex(ImageError,'cached_document_content_changed'):await self.run_pipeline()
        (self.out/'document.json').write_text(json.dumps(document),encoding='utf-8')
        Path(document['cover_path']).write_bytes(b'tampered')
        with self.assertRaises(Exception):await self.run_pipeline()
        self.assertEqual(len(self.provider.calls),calls)

    async def test_image_reservation_blocks_concurrent_paid_text_before_network(self):
        ledger=self.pipeline.ledger;ledger.run('other','other-binding',1)
        ledger.reserve('image-in-flight','gemini','image-test',.99,run_id='other')
        function=Mock(return_value={})
        with self.assertRaises(BudgetExceeded):
            await Service(self.c).model_call(function,provider='gemini',model='review-test')
        function.assert_not_called()

    async def test_partial_usage_text_never_releases_reservation(self):
        from nailblog import gemini_text
        service=Service(self.c)
        def complete(*args,usage_sink):
            usage_sink(gemini_text.usage_of(envelope([{'text':'{}'}],meta={'promptTokenCount':100})))
            return {}
        await service.model_call(complete,provider='gemini',model='review-test')
        with service.state.connection() as db:row=db.execute("SELECT * FROM paid_intents WHERE id LIKE 'text-%'").fetchone()
        self.assertEqual(row['status'],'uncertain');self.assertGreater(row['reserved'],0)

    async def test_paid_text_full_schema_body_bound_precedes_network(self):
        from nailblog.text import TextModel
        from nailblog.config import TextModelConfig
        from nailblog.ai_transport import ProviderError
        c=replace(self.c,text_model=TextModelConfig(provider='deepseek',model='image-test',key_file='fixture'))
        network=Mock();model=TextModel(c,AsyncMock(),transport=network,key_reader=lambda *a,**kw:'fixture-key')
        with self.assertRaisesRegex(ProviderError,'input_too_large'):
            model._complete([{'role':'user','content':'small'}],{'description':'x'*(128*1024)},1024,8192)
        network.assert_not_called()

    async def test_config_bounds_and_price_schema_fail_closed(self):
        for body in ('output_ceiling=0','cover_size="8K"','review_input_ceiling=999999'):
            with self.assertRaises(ValueError):load(self.temp.name,image=body)
        with self.assertRaises(ValueError):load(self.temp.name,budget__image_output_prices='image=0.0')


if __name__=='__main__':unittest.main()
