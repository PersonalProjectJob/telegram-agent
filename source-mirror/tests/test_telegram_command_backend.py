import tempfile
import unittest
from source_mirror.store import Store
from source_mirror.telegram_command_backend import execute, notification
from source_mirror.telegram_queue_worker import process_one, reconcile_unknown


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name); self.addCleanup(self.store.close)

    def test_replay_does_not_undo_a_later_control(self):
        execute(self.store, 42, 10, '/source_mirror resume')
        execute(self.store, 42, 20, '/source_mirror pause')
        self.assertEqual(execute(self.store, 42, 10, '/source_mirror resume')['state'], 'running')
        self.assertTrue(self.store.paused())
        with self.assertRaisesRegex(ValueError, 'collision'):
            execute(self.store, 42, 10, '/source_mirror pause')
        with self.assertRaisesRegex(ValueError, 'out-of-order'):
            execute(self.store, 42, 15, '/source_mirror pause')
        self.assertTrue(self.store.paused())

    def test_supported_url_held_and_unknown_host_rejected(self):
        text = 'nailblog https://www.scratchmagazine.co.uk/nail_art/article/'
        self.assertEqual(execute(self.store, 42, 3, text)['state'], 'queued')
        self.assertEqual(execute(self.store, 42, 3, text)['state'], 'queued')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM telegram_crawl_queue').fetchone()[0], 1)
        with self.assertRaises(ValueError): execute(self.store, 42, 4, 'nailblog https://example.com/x')

    def test_queue_respects_pause_and_failed_crawls_are_held(self):
        execute(self.store, 42, 10, 'nailblog https://www.scratchmagazine.co.uk/nail_art/example/')
        self.assertIsNone(process_one({}, self.store, lambda *_: self.fail('paused')))
        execute(self.store, 42, 11, '/source_mirror resume')
        calls=[]
        def broken(*args):
            calls.append(1); raise ValueError('source unavailable')
        self.assertEqual(process_one({}, self.store, broken), 'held')
        self.assertIsNone(process_one({}, self.store, broken))
        self.assertEqual(len(calls),1)
        self.assertEqual(execute(self.store, 42, 12, '/source_mirror status')['queue'], {'held':1})
        self.assertEqual(execute(self.store, 42, 13, '/source_mirror status')['articles'], {'ready':0,'held':0})

    def test_queue_targets_captured_article_and_never_retries_success(self):
        execute(self.store,42,10,'/source_mirror resume')
        execute(self.store,42,11,'nailblog https://www.scratchmagazine.co.uk/nail_art/example/')
        seen=[]
        def crawl(*_): return {'state':'captured','article_id':'a'*64}
        def publish(cfg,store,article_id):
            seen.append(article_id); return 'https://nailhub.ai/blog/example'
        self.assertEqual(process_one({},self.store,crawl,publish),'published')
        self.assertIsNone(process_one({},self.store,crawl,publish))
        self.assertEqual(seen,['a'*64])
        item=notification(self.store,42,'claim')
        self.assertEqual(item['payload']['state'],'published')
        self.assertIsNone(notification(self.store,42,'claim'))
        self.assertEqual(notification(self.store,42,'recover')['recovered_unknown'],1)
        self.assertIsNone(notification(self.store,42,'claim'))

    def test_crash_processing_is_unknown_never_retried(self):
        execute(self.store,42,1,'/source_mirror resume')
        execute(self.store,42,2,'nailblog https://www.scratchmagazine.co.uk/nail_art/example/')
        self.store.db.execute("UPDATE telegram_crawl_queue SET state='processing'")
        self.assertIsNone(process_one({},self.store,lambda *_: self.fail('must not retry')))
        self.assertEqual(execute(self.store,42,3,'/source_mirror status')['queue'],{'unknown':1})
        self.assertEqual(notification(self.store,42,'claim')['payload']['state'],'unknown')
        self.assertEqual(reconcile_unknown(self.store,42,2)['state'],'held')

    def test_reconcile_never_treats_unknown_post_as_not_created(self):
        execute(self.store,42,1,'nailblog https://www.scratchmagazine.co.uk/nail_art/example/')
        aid='a'*64
        self.store.db.execute("INSERT INTO articles VALUES (?,?,?,NULL,'ready',NULL,?,?)",(aid,'https://www.scratchmagazine.co.uk/nail_art/example/','title','time','hash'))
        self.store.db.execute("UPDATE telegram_crawl_queue SET state='unknown',article_id=?",(aid,))
        self.store.db.execute("INSERT INTO attempts(article_id,reserved_day,state,created_at) VALUES (?,'2026-10-30','unknown','time')",(aid,))
        with self.assertRaisesRegex(ValueError,'attempt requires reconciliation'):
            reconcile_unknown(self.store,42,1)
        self.store.db.execute("UPDATE attempts SET state='published',target_url='https://nailhub.ai/blog/item'")
        self.assertEqual(reconcile_unknown(self.store,42,1),{'state':'published','result_url':'https://nailhub.ai/blog/item'})

    def test_reconcile_supersedes_pending_unknown_and_notifies_resolution(self):
        execute(self.store,42,1,'/source_mirror resume')
        execute(self.store,42,2,'nailblog https://www.scratchmagazine.co.uk/nail_art/example/')
        self.store.db.execute("UPDATE telegram_crawl_queue SET state='processing'")
        process_one({},self.store,lambda *_: self.fail('must not run'))
        reconcile_unknown(self.store,42,2)
        item=notification(self.store,42,'claim')
        self.assertEqual(item['payload'],{'state':'held','result_url':None,'resolved':True})
        self.assertEqual(notification(self.store,42,'sent',item['notice_id'])['state'],'sent')
        self.assertIsNone(notification(self.store,42,'claim'))


if __name__ == '__main__': unittest.main()
