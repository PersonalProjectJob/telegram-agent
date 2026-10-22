"""Pause queued model dispatch without charging/retrying an unsent request."""
import asyncio
import json
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from dataclasses import replace
from functools import partial

from nailblog.images.pipeline import DispatchPaused, ImagePipeline
from tests.test_cloudflare_images import config as free_config, proof, FixtureCloudflare, NOW, GENERATION
from tests.test_real_images import config as paid_config, FixtureProvider, article, fixture_cover
from nailblog.config import BudgetConfig, TextModelConfig
from nailblog.service import Service
from nailblog.text import TextModel, object_schema, string_schema
from nailblog.ai_transport import ProviderError
from tests.helpers import settings
from tests.fakes import deepseek_envelope


class ImagePauseTests(unittest.IsolatedAsyncioTestCase):
    async def test_pause_while_queued_cancels_only_unsent_reservation_and_same_intent_can_resume(self):
        for free,review in ((False,False),(False,True),(True,False),(True,True)):
            with self.subTest(provider='cloudflare' if free else 'gemini',review=review), tempfile.TemporaryDirectory() as d:
                config=free_config(d) if free else paid_config(d)
                if free:proof(config)
                provider=FixtureCloudflare(config) if free else FixtureProvider(config)
                pipeline=ImagePipeline(config,provider=provider,clock=lambda:NOW,cover_renderer=fixture_cover)
                pipeline.state.set_paused(False)
                def guard():
                    if pipeline.state.control()['paused']:raise RuntimeError('paused')
                pipeline.before_dispatch=guard
                pipeline.ledger.run('run','fixture-pause-binding',9000 if free else 1)
                queued,released=threading.Event(),threading.Event();number=[0]
                @contextmanager
                def lock(**_kwargs):
                    number[0]+=1
                    # Gemini first locks its countTokens preflight; block generation.
                    if number[0] == (1 if free else 2):
                        queued.set()
                        if not released.wait(10):raise RuntimeError('fixture queue timed out')
                    yield
                model=config.image.review_model if review else config.image.model_cover
                body=provider.review(b'fixture-final-webp','fixture brief',1024) if review else provider.generation('fixture nail prompt','2K',2048)
                with patch('nailblog.images.pipeline.MODEL_LOCK.enter',side_effect=lock):
                    task=asyncio.create_task(pipeline.paid('run','intent',model,body,4096,2048,image=not review))
                    self.assertTrue(await asyncio.to_thread(queued.wait,10))
                    self.assertEqual(pipeline.ledger.get('intent')['status'],'pending')
                    pipeline.state.set_paused(True);released.set()
                    with self.assertRaisesRegex(DispatchPaused,'workflow_paused_before_dispatch'):await task
                    self.assertEqual(provider.calls,[])
                    self.assertIsNone(pipeline.ledger.get('intent'))
                    with pipeline.ledger.state.connection() as db:
                        history=db.execute('SELECT reason,reservation FROM image_dispatch_cancellations').fetchall()
                    self.assertEqual(len(history),1)
                    self.assertEqual(history[0]['reason'],'workflow_paused_before_dispatch')
                    self.assertGreater(json.loads(history[0]['reservation'])['reserved'],0)
                    pipeline.state.set_paused(False)
                    await pipeline.paid('run','intent',model,body,4096,2048,image=not review)
                self.assertEqual(len(provider.calls),1)
                self.assertIn(pipeline.ledger.get('intent')['status'],('settled','estimated'))
                with self.assertRaisesRegex(ValueError,'only_undispatched_pending'):
                    pipeline.cancel_undispatched('intent')

    async def test_pause_before_review_resumes_final_webp_without_regenerating_charged_image(self):
        with tempfile.TemporaryDirectory() as d:
            config=free_config(d);proof(config);provider=FixtureCloudflare(config)
            pipeline=ImagePipeline(config,provider=provider,clock=lambda:NOW,cover_renderer=fixture_cover)
            pipeline.state.set_paused(False)
            def guard():
                if pipeline.state.control()['paused']:raise RuntimeError('paused')
            pipeline.before_dispatch=guard
            complete=provider.complete;once=[True]
            def pause_after_generation(model,body):
                response=complete(model,body)
                if model==GENERATION and once[0]:
                    once[0]=False;pipeline.state.set_paused(True)
                return response
            provider.complete=pause_after_generation
            output=Path(d)/'output'
            with self.assertRaises(DispatchPaused):
                await pipeline.run(article(),output,'run',9000,free_enabled=True)
            manifest=json.loads((output/'manifest.json').read_text())
            cover=manifest['candidates']['cover-r0-c0']
            self.assertEqual(cover['status'],'review_paused')
            self.assertEqual(len(provider.calls),1)
            self.assertIn(pipeline.ledger.get(cover['intent'])['status'],('settled','estimated'))
            self.assertIsNone(pipeline.ledger.get(cover['review_intent']))
            first_sha=cover['sha']
            pipeline.state.set_paused(False)
            result=await pipeline.run(article(),output,'run',9000,free_enabled=True)
            self.assertEqual(result['image_hashes'][0],first_sha)
            self.assertEqual(sum(model==GENERATION for model,_ in provider.calls),4)
            self.assertEqual(len(provider.calls),8)
            self.assertFalse(any(row['status'] in ('pending','uncertain') for row in pipeline.ledger.report('run')))


class TextPauseTests(unittest.IsolatedAsyncioTestCase):
    def make(self,d,provider='deepseek'):
        c=settings(d,budget=BudgetConfig(1,{'fixture':(.5,2)}),
                   text_model=TextModelConfig(provider=provider,model='fixture',key_file=str(Path(d)/'fixture-key.json')))
        service=Service(c);service.state.set_paused(False)
        def guard():
            if service.state.control()['paused']:raise RuntimeError('paused')
        return c,service,guard

    async def test_text_queued_pause_releases_known_unsent_paid_reservation_and_resume_dispatches_once(self):
        with tempfile.TemporaryDirectory() as d:
            config,service,guard=self.make(d)
            queued,released=threading.Event(),threading.Event();calls=[]
            @contextmanager
            def lock(**_kwargs):
                queued.set()
                if not released.wait(10):raise RuntimeError('fixture timeout')
                yield
            def complete(*,usage_sink):
                calls.append('sent');usage_sink({'prompt_tokens':10,'completion_tokens':20});return {}
            with patch('nailblog.service.MODEL_LOCK.enter',side_effect=lock):
                task=asyncio.create_task(service.model_call(complete,provider='deepseek',model='fixture',before_dispatch=guard))
                self.assertTrue(await asyncio.to_thread(queued.wait,10))
                service.state.set_paused(True);released.set()
                with self.assertRaises(DispatchPaused):await task
                self.assertEqual(calls,[])
                with service.state.connection() as db:
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM paid_intents').fetchone()[0],0)
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM image_dispatch_cancellations').fetchone()[0],1)
                service.state.set_paused(False)
                await service.model_call(complete,provider='deepseek',model='fixture',before_dispatch=guard)
            self.assertEqual(calls,['sent'])

    async def test_pause_between_text_sections_stops_the_next_transport_without_another_charge(self):
        with tempfile.TemporaryDirectory() as d:
            config,service,guard=self.make(d);calls=[]
            def transport(*_args):
                calls.append('sent');service.state.set_paused(True)
                return deepseek_envelope({'text':'fixture'})
            text=TextModel(config,partial(service.model_call,before_dispatch=guard),transport=transport,
                           key_reader=lambda *a,**kw:'fixture-only',before_dispatch=guard)
            schema=object_schema({'text':string_schema(1)})
            self.assertEqual(await text.ask('one','system','user',schema,max_tokens=100),{'text':'fixture'})
            with self.assertRaises(DispatchPaused):await text.ask('two','system','user',schema,max_tokens=100)
            self.assertEqual(calls,['sent'])
            with service.state.connection() as db:
                self.assertEqual([r[0] for r in db.execute('SELECT status FROM paid_intents')],['settled'])

    async def test_gemini_pause_before_retry_retains_prior_uncertain_reservation(self):
        with tempfile.TemporaryDirectory() as d:
            config,service,guard=self.make(d,'gemini');calls=[]
            def transport(*_args):
                calls.append('sent');service.state.set_paused(True);raise ProviderError('rate_limited')
            text=TextModel(config,partial(service.model_call,before_dispatch=guard),transport=transport,
                           key_reader=lambda *a,**kw:'fixture-only',before_dispatch=guard)
            with patch('nailblog.gemini_text.time.sleep',return_value=None):
                with self.assertRaises(DispatchPaused):
                    await text.ask('one','system','user',object_schema({'text':string_schema(1)}),max_tokens=100)
            self.assertEqual(calls,['sent'])
            with service.state.connection() as db:
                rows=db.execute('SELECT status,reserved FROM paid_intents').fetchall()
                self.assertEqual(rows[0]['status'],'uncertain');self.assertGreater(rows[0]['reserved'],0)


if __name__=='__main__':unittest.main()
