import tempfile
import unittest
from pathlib import Path
from source_mirror.official_generation_state import GenerationState

class GenerationStateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.s='a'*64
    def open(self):
        state=GenerationState(self.root);self.addCleanup(state.close);return state
    def test_quality_hold_and_interrupted_never_retry(self):
        state=self.open();self.assertEqual(state.claim('source',self.s)[0],'generate')
        state.finish('source',self.s)
        self.assertEqual(state.claim('source',self.s)[0],'held')
        other=self.open();self.assertEqual(other.claim('source','b'*64)[0],'generate')
        self.assertEqual(state.claim('source','b'*64)[0],'held')
        self.assertEqual(other.claim('source','b'*64)[0],'held')
    def test_failure_is_not_automatically_retried(self):
        state=self.open();state.claim('source',self.s);state.finish('source',self.s)
        self.assertEqual(state.claim('source',self.s)[0],'held')
        self.assertEqual(state.db.execute('SELECT attempts FROM generation').fetchone()[0],1)
    def test_success_is_durable_and_snapshot_specific(self):
        state=self.open();state.claim('source',self.s);state.finish('source',self.s,draft_sha='c'*64)
        other=self.open();self.assertEqual(other.claim('source',self.s),('ready','c'*64))
        self.assertEqual(other.claim('source','d'*64)[0],'generate')
        with self.assertRaises(ValueError):other.finish('source',self.s,draft_sha='c'*64)
    def test_rotation_is_durable_and_skips_published_held_and_staged(self):
        state=self.open();sources=['one','two','three']
        self.assertEqual(state.select_source(sources,set()),'one')
        state.claim('two',self.s);state.finish('two',self.s)
        other=self.open();self.assertEqual(other.select_source(sources,set()),'three')
        self.assertEqual(state.select_source(sources,{'one'}),'three')
        state.claim('three',self.s);state.finish('three',self.s,draft_sha='b'*64)
        state.claim('stage:three',self.s);state.finish('stage:three',self.s,draft_sha='b'*64)
        self.assertIsNone(other.select_source(sources,{'one'}))
    def test_probe_new_snapshot_unlocks_only_new_identity(self):
        state=self.open();state.claim('source',self.s);state.finish('source',self.s)
        self.assertIsNone(state.select_source(['source'],set()))
        self.assertEqual(state.select_probe(['source'],set()),'source')
        state.record_probe('source',self.s)
        self.assertIsNone(state.select_source(['source'],set()))
        state.record_probe('source','b'*64)
        self.assertEqual(state.select_source(['source'],set()),'source')
        self.assertEqual(state.claim('source',self.s)[0],'held')
        self.assertEqual(state.claim('source','b'*64)[0],'generate')
        state.finish('source','b'*64)
        self.assertIsNone(state.select_source(['source'],set()))
    def test_expired_staged_head_requires_identity_and_restage_hold_blocks(self):
        state=self.open();state.claim('source',self.s);state.finish('source',self.s,draft_sha='b'*64)
        state.claim('stage:source',self.s);state.finish('stage:source',self.s,draft_sha='b'*64)
        self.assertIsNone(state.select_source(['source'],set(),{'source':'c'*64}))
        self.assertEqual(state.select_source(['source'],set(),{'source':self.s}),'source')
        phase='restage:source:2026-10-30:12:00';state.claim(phase,self.s);state.finish(phase,self.s)
        self.assertIsNone(state.select_source(['source'],set(),{'source':self.s}))
