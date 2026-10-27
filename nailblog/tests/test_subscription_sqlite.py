"""Offline WAL/actual-record regressions. No native agent invocation or live DB."""
from contextlib import closing
import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from nailblog.images import ImageError
from nailblog.images.agy_native import native_call,native_image,final_text,fields
from nailblog.images import subscription_sqlite as reader
from tests.test_subscription import proto

FIXTURES=Path(__file__).parent/'fixtures/subscription'
SHAS=('c72303fb82e04a6c825e3e51e912f8f35061ed39073fa7d0b53b56d18bac237c',
      '6afd2db4779be7a07775f7e9fa8e4bf4c2fd8c1966685683024a6cac836e3328',
      'c5ddc994c6d9c879ff0d15c5619d2547a60906952803d866081a872241de7fd3',
      '4b4b2354e3e763cbe06b377f5b3a46c1b10a54c7b4953920998f5b9abfb3e87a')


def actual_rows():
    return [(i,kind,3,0,(FIXTURES/f'agy-1.2.11-worker-step-{i}.bin').read_bytes(),None,None)
            for i,kind in enumerate((14,15,132,15))]


def create_steps(db):
    db.execute('CREATE TABLE steps(idx INTEGER PRIMARY KEY,step_type INTEGER,status INTEGER,step_format INTEGER,step_payload BLOB,permissions BLOB,error_details BLOB)')


def populate(db):
    db.executemany('INSERT INTO steps VALUES(?,?,?,?,?,?,?)',actual_rows());db.commit()


class ActualRecords(unittest.TestCase):
    def test_exact_real_native_call_image_final_text_have_distinct_shapes(self):
        rows=actual_rows()
        for row,sha in zip(rows,SHAS):self.assertEqual(hashlib.sha256(row[4]).hexdigest(),sha)
        prompt='Photorealistic editorial cover image showing elegant autumn nail art, clean salon background, no text, no logos, no watermark'
        image_name='img_ccc058341f0456fcb16e'
        call=native_call(rows[1][4],prompt=prompt,image_name=image_name)
        result=native_image(rows[2][4],conversation='9dba1449-eecc-4dcd-ab43-0deb9e0a3063',prompt=prompt,
                            image_name=image_name,brain='/home/startechai/snap/antigravity-cli/common/.gemini/antigravity-cli/brain')
        self.assertEqual(call,result['tool_call_id'])
        self.assertEqual(result['backend_model'],'gemini-3.1-flash-image')
        self.assertIn('successfully generated',final_text(rows[3][4]))
        with self.assertRaises(ImageError):final_text(rows[1][4])
        with self.assertRaises(ImageError):native_call(rows[3][4],prompt=prompt,image_name=image_name)
        for raw in (rows[3][4][:-1],rows[3][4]+b'\x08\x0f',b'SUCCESS image generated'):
            with self.assertRaises(ImageError):final_text(raw)

    def test_final_text_rejects_tool_metadata_calls_mismatch_and_extra_fields(self):
        raw=actual_rows()[3][4];root=fields(raw);body=fields(root[20][0])
        def encode(message):return proto(**{'f'+str(k):v[0] for k,v in message.items()})
        variants=[]
        for number,value in ((7,b'run_command'),(99,b'extra'),(8,b'different text')):
            changed=dict(body);changed[number]=[value]
            variants.append(encode({**root,20:[encode(changed)]}))
        variants.append(encode({**root,5:[root[5][0]+proto(f4=proto(f2=b'run_command'))]}))
        for changed in variants:
            with self.subTest(sha=hashlib.sha256(changed).hexdigest()):
                with self.assertRaises(ImageError):final_text(changed)


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.root.chmod(0o700)
        self.native=self.root/'native';self.native.mkdir(mode=0o700)
        self.private=self.root/'private';self.private.mkdir(mode=0o700)
        self.path=self.native/'conversation.db'

    def database(self,wal=False):
        db=sqlite3.connect(self.path)
        self.addCleanup(db.close)
        if wal:
            self.assertEqual(db.execute('PRAGMA journal_mode=WAL').fetchone(),('wal',))
            db.execute('PRAGMA wal_autocheckpoint=0')
        create_steps(db);db.commit()
        if wal:db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        populate(db)
        self.path.chmod(0o600)
        wal_path=Path(str(self.path)+'-wal')
        if wal_path.exists():wal_path.chmod(0o600)
        return db

    def test_committed_nonempty_wal_is_replayed_only_in_private_copy(self):
        db=self.database(wal=True);wal=Path(str(self.path)+'-wal');shm=Path(str(self.path)+'-shm')
        self.assertEqual(self.path.read_bytes()[18:20],b'\2\2')
        self.assertGreater(wal.stat().st_size,32)
        before={p.name:(p.read_bytes(),p.stat().st_ino) for p in (self.path,wal,shm)}
        with self.assertRaises(sqlite3.Error),closing(sqlite3.connect(':memory:')) as memory:
            memory.deserialize(self.path.read_bytes());memory.execute('SELECT count(*) FROM steps').fetchone()
        rows,metadata=reader.snapshot_rows(self.path,self.private)
        self.assertEqual(rows,actual_rows())
        self.assertEqual(metadata['wal_sha'],hashlib.sha256(wal.read_bytes()).hexdigest())
        self.assertEqual(list(self.private.iterdir()),[])
        self.assertEqual(before,{p.name:(p.read_bytes(),p.stat().st_ino) for p in (self.path,wal,shm)})

    def test_wal_header2_with_empty_or_absent_sidecar_and_legacy_db_work(self):
        db=self.database(wal=True);db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        wal=Path(str(self.path)+'-wal');self.assertEqual(wal.stat().st_size,0)
        rows,metadata=reader.snapshot_rows(self.path,self.private)
        self.assertEqual(rows,actual_rows());self.assertEqual(metadata['wal_bytes'],0)
        # Closing is done by the fixture's SQLite writer, not the snapshot reader.
        db.close();self.assertFalse(wal.exists())
        rows,metadata=reader.snapshot_rows(self.path,self.private)
        self.assertEqual(rows,actual_rows());self.assertIsNone(metadata['wal_sha'])

    def test_plain_rollback_journal_db_remains_supported(self):
        db=self.database();db.close()
        rows,metadata=reader.snapshot_rows(self.path,self.private)
        self.assertEqual(rows,actual_rows());self.assertIsNone(metadata['wal_sha'])

    def test_corrupt_wal_checksum_and_truncated_frames_are_rejected(self):
        self.database(wal=True);wal=Path(str(self.path)+'-wal');valid=wal.read_bytes()
        for changed in (valid[:40],valid[:30],valid[:-1],valid[:-1]+bytes([valid[-1]^1])):
            with self.subTest(size=len(changed)):
                wal.write_bytes(changed)
                with self.assertRaises(ImageError):reader.snapshot_rows(self.path,self.private)
        wal.write_bytes(valid)

    def test_mutation_or_wal_presence_flip_between_full_captures_fails_closed(self):
        self.database().close();original=reader._capture
        kinds=('db_mutation','wal_appears','db_replaced') if os.name=='posix' else ('db_mutation','wal_appears')
        for kind in kinds:
            with self.subTest(kind=kind):
                wal=Path(str(self.path)+'-wal');wal.unlink(missing_ok=True)
                before=self.path.read_bytes();calls=[]
                def changed(*a,**kw):
                    value=original(*a,**kw);calls.append(1)
                    if len(calls)==1:
                        if kind=='db_mutation':
                            with self.path.open('r+b') as stream:stream.seek(100);stream.write(b'X')
                        elif kind=='wal_appears':wal.write_bytes(b'')
                        else:
                            self.path.rename(self.path.with_suffix('.old'));self.path.write_bytes(before);self.path.chmod(0o600)
                    return value
                with patch.object(reader,'_capture',side_effect=changed):
                    with self.assertRaises(ImageError):reader.snapshot_rows(self.path,self.private)
                self.path.write_bytes(before);self.path.chmod(0o600)

    def test_large_db_wal_combined_and_growth_are_bounded_while_reading(self):
        self.database();wal=Path(str(self.path)+'-wal')
        with self.path.open('r+b') as stream:stream.truncate(reader.MAX_BYTES+1)
        with self.assertRaises(ImageError):reader.capture(self.path)
        # Cheap lower cap proves combined size and a growing stream are checked,
        # without allocating an oversized data buffer in the test itself.
        self.path.write_bytes(b'x'*100);wal.write_bytes(b'y'*100);wal.chmod(0o600)
        with patch.object(reader,'MAX_BYTES',150):
            with self.assertRaisesRegex(ImageError,'bounds'):reader.capture(self.path)
        wal.unlink();self.path.write_bytes(b'x'*100)
        real_read=os.read;count=[]
        def grow(fd,n):
            result=real_read(fd,n);count.append(1)
            if len(count)==1:
                with self.path.open('ab') as stream:stream.write(b'y'*200)
            return result
        with patch.object(reader,'MAX_BYTES',150),patch.object(reader.os,'read',side_effect=grow):
            with self.assertRaisesRegex(ImageError,'bounds'):reader.capture(self.path)

    @unittest.skipUnless(os.name=='posix','POSIX nofollow sidecar and source descriptors')
    def test_db_and_wal_symlinks_or_hardlinks_are_rejected(self):
        self.database();target=self.native/'other';target.write_bytes(b'');target.chmod(0o600)
        wal=Path(str(self.path)+'-wal');wal.symlink_to(target)
        with self.assertRaises(ImageError):reader.capture(self.path)
        wal.unlink();os.link(target,wal)
        with self.assertRaises(ImageError):reader.capture(self.path)
        wal.unlink();self.path.rename(self.native/'real.db');self.path.symlink_to(self.native/'real.db')
        with self.assertRaises(ImageError):reader.capture(self.path)

    def test_capture_deadline_query_progress_and_payload_limits_are_enforced(self):
        self.database()
        times=iter((0,100))
        with self.assertRaisesRegex(ImageError,'deadline'):reader.capture(self.path,clock=lambda:next(times))
        db=self.private/'large.db'
        with closing(sqlite3.connect(db)) as writer:
            create_steps(writer)
            writer.execute('INSERT INTO steps VALUES(0,14,3,0,?,NULL,NULL)',(b'x'*(2*1024*1024+1),))
            writer.commit()
        with self.assertRaises(sqlite3.Error):reader._query(db,clock=lambda:0,until=100)
        # Force real SQLite VM progress work past a fixed deadline.
        with closing(sqlite3.connect(db)) as writer:
            writer.execute('DELETE FROM steps')
            writer.executemany('INSERT INTO steps VALUES(?,14,3,0,?,NULL,NULL)',[(i,b'x') for i in range(2000)])
            writer.commit()
        with self.assertRaises(sqlite3.Error):reader._query(db,clock=lambda:100,until=1)

    def test_non_table_steps_and_excess_rows_fail_closed(self):
        db=sqlite3.connect(self.path);self.addCleanup(db.close)
        db.execute('CREATE VIEW steps AS SELECT 0 AS idx');db.commit();self.path.chmod(0o600)
        with self.assertRaisesRegex(ImageError,'steps_schema'):reader.snapshot_rows(self.path,self.private)
        db.execute('DROP VIEW steps');create_steps(db)
        db.executemany('INSERT INTO steps VALUES(?,14,3,0,?,NULL,NULL)',[(i,b'x') for i in range(65)]);db.commit()
        with self.assertRaisesRegex(ImageError,'step_bounds'):reader.snapshot_rows(self.path,self.private)
