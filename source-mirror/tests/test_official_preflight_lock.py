import os
import tempfile
import unittest
import json
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import MagicMock,patch
from source_mirror.lock import worker_lock
from source_mirror.official_reservation_preflight import scan_admin
from source_mirror.official_sources import SOURCES
from source_mirror.official_scheduler import try_once

class PreflightLockTests(unittest.TestCase):
    @unittest.skipUnless(os.name=='posix','Linux flock ownership required')
    def test_scheduler_owned_lock_reaches_scan_and_unowned_fails(self):
        source=next(x['url'] for x in SOURCES if x['id']=='cdc-niosh-nail-technicians')
        with tempfile.TemporaryDirectory() as root:
            with worker_lock(root):pass
            with self.assertRaisesRegex(ValueError,'lock not owned'):scan_admin(root,source,lock_held=True)
            playwright=MagicMock();context=MagicMock();context.pages=[];playwright.chromium.launch_persistent_context.return_value=context
            with patch('playwright.sync_api.sync_playwright',return_value=nullcontext(playwright)), \
                 patch('source_mirror.official_reservation_preflight.isolated_profile',return_value=nullcontext(root)), \
                 patch('source_mirror.official_reservation_preflight.check',return_value={'status':'SOURCE_ABSENT_NONATOMIC'}) as check:
                with worker_lock(root):result=scan_admin(root,source,lock_held=True)
                self.assertEqual(result['status'],'SOURCE_ABSENT_NONATOMIC');check.assert_called_once()

    @unittest.skipUnless(os.name=='posix','Linux flock ownership required')
    def test_scheduler_preflight_scan_with_owned_live_lock_before_reserve(self):
        source=next(x['url'] for x in SOURCES if x['id']=='cdc-niosh-nail-technicians')
        with tempfile.TemporaryDirectory() as root:
            root=Path(root);folder=root/'official-auto-evidence'/'cdc-niosh-nail-technicians'/'a'/'b'
            row={'evidence_path':str(folder),'record_sha':'b'};candidate={'id':'article','evidence_path':str(folder)}
            table=MagicMock();table.fetchone.return_value=(1,);query=MagicMock();query.fetchall.return_value=[candidate];item=MagicMock();item.fetchone.return_value=row
            store=MagicMock();store.root=root;store.db.execute.side_effect=[table,query,item]
            playwright=MagicMock();context=MagicMock();context.pages=[];playwright.chromium.launch_persistent_context.return_value=context
            payload=json.dumps({'published_at':'2026-10-30T20:00:00+07:00','source':{'url':source}}).encode()
            with patch('source_mirror.official_store.OFFICIAL_RESERVATION_ENABLED',True), \
                 patch('source_mirror.official_scheduler.scheduled',return_value=nullcontext()), \
                 patch('source_mirror.official_scheduler.load_publish_policy',return_value={'enabled':True,'source_ids':['cdc-niosh-nail-technicians']}), \
                 patch('source_mirror.official_publisher.available',return_value=True), \
                 patch('source_mirror.official_publisher.publish',return_value={'verified':True,'url':'https://nailhub.ai/blog/fixture','published_day':'2026-10-30'}), \
                 patch('source_mirror.official_store.reserve',return_value=(1,{'id':'article'})) as reserve, \
                 patch('source_mirror.official_reservation_preflight.require'), \
                 patch('source_mirror.official_reservation_preflight._check_candidate_hashes'), \
                 patch('source_mirror.official_reservation_preflight._validated',return_value=(payload,None,None)), \
                 patch('source_mirror.official_reservation_preflight.check_publish_time'), \
                 patch('source_mirror.official_reservation_preflight.isolated_profile',return_value=nullcontext(str(root))), \
                 patch('playwright.sync_api.sync_playwright',return_value=nullcontext(playwright)), \
                 patch('source_mirror.official_reservation_preflight.check',return_value={'status':'SOURCE_ABSENT_NONATOMIC'}) as scan:
                with worker_lock(root):self.assertEqual(try_once(store,'2026-10-30','20:00'),'https://nailhub.ai/blog/fixture')
                scan.assert_called_once();reserve.assert_called_once();store.published.assert_called_once()
