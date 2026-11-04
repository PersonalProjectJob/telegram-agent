import hashlib
import base64
import json
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock
import tempfile
import asyncio
import os
import socket
import unittest

from nailhub.source_mirror_review import ReviewTransport, sha, SOURCES, GLOSSARY, start_server


class ReviewTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.db = str(Path(self.temp.name) / 'review.sqlite')
        url, category, scope, label = SOURCES['cdc-niosh-nail-technicians']
        candidate = {'title_vi':'An toàn làm việc', 'text_vi':'Hướng dẫn an toàn.', 'uncertainties':[]}
        review = {'verdict':'PASS','critical':0,'major':0,'minor':0,'issues':[],'uncertainties':[], 'glossary_ok':True,'accuracy_ok':True,'technical_clarity_ok':True}
        html = b'<html><head><title>Safety</title></head><body><main><p>Safety guidance.</p></main></body></html>'
        source = {'metadata': {'source_id':'cdc-niosh-nail-technicians','source_url':url,'category':category,'geographic_scope':scope,'source_sha256':hashlib.sha256(html).hexdigest(),'reference_text_sha256':sha('Safety guidance.\n'),'title':'Safety','version':1,'status':'expert_review_required'}, 'reference_text':'Safety guidance.\n', 'source_label':label, 'source_html_base64':base64.b64encode(html).decode('ascii')}
        self.raw = json.dumps({'version':'official-review-v1','status':'HOLD','owner_approval':None,'specialist_review':None,'source':source,'candidate':candidate,'automated_mqm':review,'generation_envelope':{'status':'SUCCESS','response':json.dumps(candidate)},'review_envelope':{'status':'SUCCESS','response':json.dumps(review)},'model':'gemini-3.8-flash-medium','effort':'medium','glossary':GLOSSARY,'generation_prompt_sha256':'a'*64,'review_prompt_sha256':'b'*64})
        self.client = SimpleNamespace(send_file=AsyncMock(), get_messages=AsyncMock(), download_media=AsyncMock())
        self.transport = ReviewTransport(self.db, 123, self.client)
        self.addCleanup(self.transport.close)

    async def submit(self):
        async def send(owner, file, caption, parse_mode):
            self.caption = caption; self.file = file.getvalue()
            return SimpleNamespace(id=42)
        self.client.send_file.side_effect = send
        await self.transport.submit(self.raw, sha(self.raw))
        return self.transport.db.execute('SELECT nonce FROM source_mirror_reviews').fetchone()[0]

    def event(self, nonce, **overrides):
        user = SimpleNamespace(id=123, bot=False, is_self=False)
        msg = SimpleNamespace(id=50, reply_to_msg_id=42, fwd_from=None, edit_date=None)
        event = SimpleNamespace(raw_text='/source-mirror approve '+nonce, message=msg, sender_id=123, chat_id=123,
                                is_private=True, out=False, fwd_from=None,
                                get_sender=AsyncMock(return_value=user), get_chat=AsyncMock(return_value=user))
        for key, value in overrides.items(): setattr(event, key, value)
        self.client.get_messages.side_effect = lambda owner, ids: (SimpleNamespace(out=True, edit_date=None, raw_text=self.caption) if ids == 42 else SimpleNamespace(raw_text=event.raw_text, edit_date=None, reply_to_msg_id=42, fwd_from=None))
        self.client.download_media.return_value = self.file
        return event

    def state(self): return self.transport.db.execute('SELECT state FROM source_mirror_reviews').fetchone()[0]

    async def test_bound_owner_decision_and_replay(self):
        nonce = await self.submit(); event = self.event(nonce)
        text = self.file.decode('utf-8')
        self.assertIn('Hướng dẫn an toàn.', text); self.assertIn('Safety guidance.', text)
        self.assertIn('https://www.cdc.gov/niosh/nail-technicians/about/index.html', text)
        self.assertNotIn('source_html_base64', text); self.assertNotIn(json.loads(self.raw)['source']['source_html_base64'], text)
        self.assertIn('Readable SHA256: ' + hashlib.sha256(self.file).hexdigest(), self.caption)
        self.assertIn('SHA256: ' + sha(self.raw), self.caption)
        await self.transport.handle(event); self.assertEqual(self.state(), 'approve')
        await self.transport.handle(event); self.assertEqual(self.state(), 'approve')
        with self.assertRaises(ValueError): await self.transport.submit(self.raw, sha(self.raw))

    async def test_unauthorized_forward_stale_tamper_and_edit_held(self):
        nonce = await self.submit()
        for options in ({'sender_id': 999}, {'chat_id': 999}, {'fwd_from': object()}, {'is_private': False}):
            await self.transport.handle(self.event(nonce, **options)); self.assertEqual(self.state(), 'pending')
        event = self.event(nonce); event.message.edit_date = object()
        await self.transport.handle(event); self.assertEqual(self.state(), 'pending')
        event = self.event(nonce); self.client.download_media.return_value = b'tampered'
        await self.transport.handle(event); self.assertEqual(self.state(), 'pending')
        event = self.event(nonce); self.caption = self.caption.replace('Readable SHA256:', 'Changed SHA256:')
        await self.transport.handle(event); self.assertEqual(self.state(), 'pending')
        self.transport.db.execute('UPDATE source_mirror_reviews SET expires=0')
        await self.transport.handle(self.event(nonce)); self.assertEqual(self.state(), 'pending')

    async def test_unknown_send_is_not_retried(self):
        self.client.send_file.side_effect = TimeoutError()
        with self.assertRaises(TimeoutError): await self.transport.submit(self.raw, sha(self.raw))
        self.assertEqual(self.state(), 'unknown')
        with self.assertRaises(ValueError): await self.transport.submit(self.raw, sha(self.raw))

    async def test_bad_hash_and_tampered_database_rejected(self):
        with self.assertRaises(ValueError): await self.transport.submit(self.raw, 'b'*64)
        nonce = await self.submit(); self.transport.db.execute("UPDATE source_mirror_reviews SET artifact='{}'")
        await self.transport.handle(self.event(nonce)); self.assertEqual(self.state(), 'pending')

    async def test_forged_incomplete_artifact_and_source_rejected(self):
        for raw in ('{}', json.dumps(dict(json.loads(self.raw), source={})),
                    json.dumps(dict(json.loads(self.raw), candidate={})),
                    json.dumps(dict(json.loads(self.raw), automated_mqm={})),
                    json.dumps(dict(json.loads(self.raw), generation_envelope={'status':'SUCCESS','response':'{}','secret':'SENTINEL'}))):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                await self.transport.submit(raw, sha(raw))
        self.client.send_file.assert_not_awaited()

    async def test_changed_html_and_forged_reference_rejected(self):
        artifact = json.loads(self.raw)
        artifact['source']['source_html_base64'] = base64.b64encode(b'<main><p>forged</p></main>').decode('ascii')
        raw = json.dumps(artifact)
        with self.assertRaises(ValueError): await self.transport.submit(raw, sha(raw))
        artifact = json.loads(self.raw); artifact['source']['reference_text'] = 'Forged guidance.\n'
        artifact['source']['metadata']['reference_text_sha256'] = sha(artifact['source']['reference_text'])
        raw = json.dumps(artifact)
        with self.assertRaises(ValueError): await self.transport.submit(raw, sha(raw))
        self.client.send_file.assert_not_awaited()


@unittest.skipUnless(hasattr(socket, 'SO_PEERCRED') and hasattr(os, 'geteuid') and os.geteuid() > 0, 'requires non-root Linux SO_PEERCRED')
class SocketTests(IsolatedAsyncioTestCase):
    async def test_peer_uid_and_stale_socket_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory)/'review.sock')
            stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            stale.bind(path); stale.close()
            transport = SimpleNamespace(submit=AsyncMock(return_value={'state':'pending','sha256':'x'}))
            server = await start_server(path, os.geteuid(), transport)
            async def request():
                reader, writer = await asyncio.open_unix_connection(path)
                writer.write(b'{"artifact":"x","sha256":"x"}\n'); await writer.drain()
                result = json.loads(await reader.readline())
                writer.close(); await writer.wait_closed()
                return result
            self.assertEqual((await request())['state'], 'pending')
            with self.assertRaises(ValueError): await start_server(path, os.geteuid(), transport)
            server.close(); await server.wait_closed()
            # The owned stale socket is recovered; an unexpected peer is denied.
            server = await start_server(path, os.geteuid() + 1, transport)
            self.assertEqual((await request())['state'], 'HOLD')
            self.assertEqual(transport.submit.await_count, 1)
            server.close(); await server.wait_closed()
