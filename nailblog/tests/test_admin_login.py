"""Cold hydration, existing-session validation and terminal sign-in outcomes."""
import asyncio
import secrets
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from playwright.async_api import TimeoutError as BrowserTimeout

from nailblog.admin import Admin
from nailblog.publication import AdminError
from tests.test_admin_phase2 import config


class LoginTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self, directory, *, status=200, existing=False):
        admin=Admin(config(directory))
        waiting,hydrate=asyncio.Event(),asyncio.Event()
        state={'button':False,'email':False,'submitted':0,'opened':0}
        async def wait(**kwargs):
            self.assertEqual(kwargs,{'state':'visible','timeout':admin.config.admin.timeout_seconds*1000})
            waiting.set()
            if existing:raise BrowserTimeout('no rendered login controls')
            await hydrate.wait();state['button']=True
        async def open_dialog():state.update(email=True,opened=state['opened']+1)
        async def submit():state['submitted']+=1
        union=SimpleNamespace(first=SimpleNamespace(wait_for=wait))
        email=SimpleNamespace(or_=lambda _:union,is_visible=AsyncMock(side_effect=lambda:state['email']),
                              wait_for=AsyncMock(),fill=AsyncMock(),count=AsyncMock(side_effect=AssertionError('racy count')))
        opening=SimpleNamespace(click=AsyncMock(side_effect=open_dialog),count=AsyncMock(side_effect=AssertionError('racy count')))
        submit_button=SimpleNamespace(click=AsyncMock(side_effect=submit))
        dialog=SimpleNamespace(get_by_role=lambda *_a,**_k:submit_button,wait_for=AsyncMock())
        class Response:
            async def __aenter__(self):return self
            async def __aexit__(self,*args):pass
            @property
            def value(self):
                async def received():return SimpleNamespace(status=status)
                return received()
        def locator(selector):
            return email if selector in ('#login-email','#login-email:visible') else SimpleNamespace(fill=AsyncMock())
        admin.page=SimpleNamespace(goto=AsyncMock(),locator=locator,
            get_by_role=lambda role,**_kwargs:dialog if role=='dialog' else opening,
            expect_response=lambda _:Response())
        admin.list_first=AsyncMock(return_value={'items':[]})
        return admin,waiting,hydrate,state,email

    async def test_cold_domcontentloaded_waits_for_hydrated_control_before_one_signin(self):
        with tempfile.TemporaryDirectory() as d:
            admin,waiting,hydrate,state,email=self.fixture(d)
            # Generated synthetic data never corresponds to a real account.
            credentials={'email':secrets.token_hex(8)+'@example.invalid','password':secrets.token_urlsafe()}
            with patch('nailblog.admin.load_credentials',return_value=credentials) as load:
                task=asyncio.create_task(admin.login())
                await asyncio.wait_for(waiting.wait(),1)
                self.assertFalse(task.done());load.assert_not_called()
                self.assertEqual(state['submitted'],0)
                hydrate.set();await task
                load.assert_called_once();self.assertEqual(credentials,{})
            self.assertEqual((state['opened'],state['submitted']),(1,1))
            email.wait_for.assert_awaited_once();admin.list_first.assert_not_awaited()

    async def test_absent_login_controls_require_fresh_authenticated_listing(self):
        with tempfile.TemporaryDirectory() as d:
            admin,*_=self.fixture(d,existing=True)
            with patch('nailblog.admin.load_credentials') as load:
                self.assertEqual(await admin.login(),{'items':[]})
                load.assert_not_called();admin.list_first.assert_awaited_once()

    async def test_absent_controls_with_unauthenticated_listing_never_claim_success_or_retry(self):
        with tempfile.TemporaryDirectory() as d:
            admin,*_=self.fixture(d,existing=True)
            admin.list_first.side_effect=AdminError('http_401')
            with patch('nailblog.admin.load_credentials') as load:
                with self.assertRaisesRegex(AdminError,'http_401'):await admin.login()
                load.assert_not_called();admin.list_first.assert_awaited_once()

    async def test_rejected_signin_or_mfa_is_terminal_no_auth_retry(self):
        for status in (401,403,202):
            with self.subTest(status=status),tempfile.TemporaryDirectory() as d:
                admin,_waiting,hydrate,state,_email=self.fixture(d,status=status);hydrate.set()
                credentials={'email':secrets.token_hex(8)+'@example.invalid','password':secrets.token_urlsafe()}
                with patch('nailblog.admin.load_credentials',return_value=credentials) as load:
                    with self.assertRaisesRegex(AdminError,'login_rejected_or_mfa'):await admin.login()
                    load.assert_called_once();self.assertEqual(credentials,{})
                self.assertEqual(state['submitted'],1);admin.list_first.assert_not_awaited()
