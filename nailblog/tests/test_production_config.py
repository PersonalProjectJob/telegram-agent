"""Offline production configuration and subscription environment gates."""
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from nailblog._errors import ConfigError
from nailblog.agy_text import AgyTextError, text_purpose
from nailblog.config import (AutoflowConfig, ContractConfig, ImageConfig, PRODUCTION_MEDIA_ORIGIN, STAGING_MEDIA_ORIGIN)
from nailblog.images import ImageError
from nailblog.images.subscription_pipeline import CLI_PROCESSING, _production_environment, run, validate_provenance
from nailblog.publication import digest
from tests.helpers import load, settings
from tests.test_config_autoflow import IDS, LIVE, live_sections

ROOT = '/var/lib/agentfleet/nailblog/prod'
CONTRACT = 'verified = true\nverification_ref = "/tmp/report.json"\nenvironment = "production"\n' + \
           f'media_origin = "{PRODUCTION_MEDIA_ORIGIN}"'


class ProductionConfigTests(unittest.TestCase):
    def load(self, *, service=None, **sections):
        values = live_sections(contract=CONTRACT,
            admin=f'daily_publish_limit = 2\nprofile_path = "{ROOT}/browser"', admin__category_ids=IDS,
            autoflow=f'enabled = true\nowner_approval = true\nruns_dir = "{ROOT}/runs"')
        values.update(sections)
        with tempfile.TemporaryDirectory() as directory:
            return load(directory, {'mode': 'live', 'db_path': ROOT + '/nailblog.sqlite3', **(service or {})}, **values)

    def test_valid_production_config_requires_manual_owner(self):
        config = self.load()
        self.assertEqual((config.contract.environment, config.contract.media_origin, config.autoflow.owner_approval),
                         ('production', PRODUCTION_MEDIA_ORIGIN, True))

    def test_mixed_or_missing_production_bindings_fail_closed(self):
        bad = [
            ({'db_path': '/var/lib/agentfleet/nailblog/nailblog.sqlite3'}, {}, 'isolated production state'),
            ({}, {'admin': 'daily_publish_limit = 2\nprofile_path = "/var/lib/agentfleet/nailblog/browser"'}, 'isolated production browser'),
            ({}, {'autoflow': 'enabled = true\nruns_dir = "/var/lib/agentfleet/nailblog/runs"'}, 'isolated production runs'),
            ({}, {'contract': CONTRACT.replace(PRODUCTION_MEDIA_ORIGIN, STAGING_MEDIA_ORIGIN)}, 'media_origin'),
            ({}, {'autoflow': f'enabled = true\nowner_approval = false\nruns_dir = "{ROOT}/runs"'}, 'owner_approval = false'),
        ]
        for service, sections, error in bad:
            with self.subTest(error=error), self.assertRaisesRegex(ConfigError, error):
                self.load(service=service, **sections)

    def test_pipeline_requires_bound_production_origins_and_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            good = settings(directory, mode='live', contract=ContractConfig(verified=True, media_origin=PRODUCTION_MEDIA_ORIGIN),
                            autoflow=AutoflowConfig(owner_approval=True))
            self.assertEqual(_production_environment(good)['environment'], 'production')
            for bad in (replace(good, autoflow=AutoflowConfig(owner_approval=False)),
                        replace(good, contract=replace(good.contract, verified=False)),
                        replace(good, contract=replace(good.contract, media_origin=STAGING_MEDIA_ORIGIN))):
                with self.assertRaises(ImageError):
                    _production_environment(bad)

    def test_provenance_rejects_other_or_missing_environment_before_receipt_access(self):
        with tempfile.TemporaryDirectory() as directory:
            config = settings(directory, mode='live', contract=ContractConfig(verified=True, media_origin=PRODUCTION_MEDIA_ORIGIN),
                              autoflow=AutoflowConfig(owner_approval=True))
            document = {'article': {'title': 'test'}, 'image_source': {'route': 'subscription', 'api_usd': None,
                                                                        'plan_cost_included': 'unknown'}}
            manifest = {'provider': 'subscription', 'processing': CLI_PROCESSING, 'document_digest': digest(document),
                        'article_sha': digest(document['article'])}
            for env in (None, {'environment': 'staging'}):
                with self.subTest(environment=env), self.assertRaisesRegex(ImageError, 'environment_mismatch'):
                    validate_provenance(document, {**manifest, 'environment': env}, Path(directory), config)

    def test_production_text_jobs_have_distinct_environment_bound_purpose(self):
        with tempfile.TemporaryDirectory() as directory:
            production = settings(directory, mode='live', contract=ContractConfig(verified=True, media_origin=PRODUCTION_MEDIA_ORIGIN))
            staging = replace(production, contract=replace(production.contract, environment='staging'))
            self.assertEqual(text_purpose(staging, 'onepost:1'), 'onepost:1')
            self.assertNotEqual(text_purpose(production, 'onepost:1'), text_purpose(staging, 'onepost:1'))
            self.assertEqual(text_purpose(production, 'onepost:1'), text_purpose(production, 'onepost:1'))
            self.assertNotEqual(text_purpose(production, 'onepost:1'), text_purpose(production, 'onepost:1#retry1'))
            with self.assertRaises(AgyTextError):
                text_purpose(replace(production, contract=replace(production.contract, media_origin=STAGING_MEDIA_ORIGIN)),
                             'onepost:1')


class ProductionPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_non_manual_approval_cannot_dispatch_or_create_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            config = settings(directory, mode='live', contract=ContractConfig(verified=True, media_origin=PRODUCTION_MEDIA_ORIGIN),
                              image=ImageConfig(candidates=1, max_rounds=1, review_bypass=True),
                              autoflow=AutoflowConfig(owner_approval=False))
            provider = AsyncMock()
            pipeline = SimpleNamespace(config=config, provider=provider)
            out = Path(directory) / 'run'
            with self.assertRaisesRegex(ImageError, 'owner_or_contract'):
                await run(pipeline, {}, out, 'run', enabled=True, paid_enabled=False, free_enabled=False)
            self.assertFalse(out.exists())
            provider.assert_not_called()
