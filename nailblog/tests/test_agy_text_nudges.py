"""Pinned agy no-tool completion nudges: native transcript and worker/producer offline regressions."""
import hashlib
import json
import tempfile
import unittest
import uuid
from pathlib import Path

from nailblog.agy_text import AgyTextError
from nailblog.images import ImageError
from nailblog.images.agy_native import (MAX_TEXT_TRANSCRIPT_BYTES, TEXT_COMPLETION_NUDGE, native_text,
                                       salvage_text, text_transcript)
from nailblog.images.subscription import verify_text_receipt
from nailblog.images.subscription_io import Directory
from tests.test_agy_text import (ARTICLE, Env, assistant_record, user_record, rows_for, write_conversation, proto)


def nudge_record(text=TEXT_COMPLETION_NUDGE, *, extra_body=False, initial_body=False, tool=False, unknown_metadata=False):
    payload = text.encode()
    metadata = dict(f1=b'timestamp',f3=4,f8=b'timestamp',f12=b'p'*36,f20=b'conversation',f26=b'event',f32=b'timestamp')
    if tool:metadata['f4']=b'call'
    if unknown_metadata:metadata['f99']=b'unknown'
    body = dict(f2=payload,f3=proto(f1=payload))
    if extra_body or initial_body:body['f4']=b''
    if initial_body:body['f12']=b'initial prompt context'
    return proto(f1=14,f4=3,f5=proto(**metadata),f19=proto(**body))


def nudged_rows(prompt='P', answer=None, count=3):
    answer = json.dumps(ARTICLE) if answer is None else answer
    rows = [(0,14,3,0,user_record(prompt),None,None), (1,15,3,0,assistant_record(answer),None,None)]
    for _ in range(count):
        rows.append((len(rows),14,3,0,nudge_record(),None,None))
        rows.append((len(rows),15,3,0,assistant_record(answer),None,None))
    return rows


def replace_record(rows, index, record):
    result = list(rows)
    result[index] = (*result[index][:4], record, *result[index][5:])
    return result


class NativeNudgeTests(unittest.TestCase):
    def test_exact_three_nudges_validate_and_salvage_with_every_raw_row_retained(self):
        rows = nudged_rows()
        native = native_text(rows,'P')
        self.assertEqual(native['steps'],[14,15,14,15,14,15,14,15])
        self.assertEqual(json.loads(native['final_text']),ARTICLE)
        self.assertEqual(native['transcript'],salvage_text(rows,'P')['transcript'])
        proof = json.dumps(native['transcript']).encode()
        self.assertEqual(text_transcript(proof),rows)

    def test_non_json_intermediate_assistant_answer_is_held_even_without_nudge(self):
        with self.assertRaises(ImageError):native_text(rows_for('P','thinking',json.dumps(ARTICLE)),'P')

    def test_unknown_or_changed_nudge_changed_answer_tools_and_bad_wire_fail_closed(self):
        good = nudged_rows()
        wrong_mirror = nudge_record().replace(TEXT_COMPLETION_NUDGE.encode(),
                                                                 b'x'*len(TEXT_COMPLETION_NUDGE),1)
        cases = {
            'changed nudge':replace_record(good,2,nudge_record(TEXT_COMPLETION_NUDGE+' ')),
            'wrong first prompt':nudged_rows('other'),
            'initial shape on nudge':replace_record(good,2,nudge_record(initial_body=True)),
            'initial record on nudge':replace_record(good,2,user_record(TEXT_COMPLETION_NUDGE)),
            'nudge shape on initial prompt':replace_record(good,0,nudge_record('P')),
            'unknown nudge body field':replace_record(good,2,nudge_record(extra_body=True)),
            'nudge metadata tool':replace_record(good,2,nudge_record(tool=True)),
            'unknown nudge metadata':replace_record(good,2,nudge_record(unknown_metadata=True)),
            'intermediate tool call':replace_record(good,3,assistant_record(json.dumps(ARTICLE),tool=True)),
            'unknown assistant wire':replace_record(good,3,assistant_record(json.dumps(ARTICLE),extra=True)),
            'malformed user wire':replace_record(good,2,b'not native protobuf'),
            'mismatched user mirror':replace_record(good,2,wrong_mirror),
            'non-json repeated answer':nudged_rows(answer='all done'),
            'multiple-json repeated answer':nudged_rows(answer=json.dumps(ARTICLE)*2),
            'consecutive nudges':replace_record(good,3,nudge_record()),
            'four nudges':nudged_rows(count=4),
            'trailing nudge':good[:-1],
        }
        # Consecutive-user variant must match its outer step type as well as raw wire type.
        item=cases['consecutive nudges'];item[3]=(3,14,3,0,item[3][4],None,None)
        for label,rows in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(ImageError):native_text(rows,'P')
                self.assertIsNone(salvage_text(rows,'P'))

    def test_nudge_permission_error_status_and_row_count_bounds_remain_fail_closed(self):
        for index in (2,3):
            for field,value in ((2,1),(3,1),(5,b'permission'),(6,b'error')):
                rows=nudged_rows(); row=list(rows[index]);row[field]=value;rows[index]=tuple(row)
                with self.subTest(index=index,field=field),self.assertRaises(ImageError):native_text(rows,'P')
        with self.assertRaises(ImageError):native_text(rows_for('P',*[json.dumps(ARTICLE)]*16),'P')
        for proof in (b'[]',b'null',b'{}',b'x'*(MAX_TEXT_TRANSCRIPT_BYTES+1)):
            with self.assertRaises(ImageError):text_transcript(proof)


class NudgeWorkerProducerTests(Env):
    def configure_nudges(self, *, timeout=False, change=False):
        def response(args, limit):
            prompt=args[args.index('--print')+1]
            rows=nudged_rows(prompt)
            if change:rows=replace_record(rows,3,assistant_record(json.dumps({'title':'Changed'})))
            session=str(uuid.uuid4())
            write_conversation(self.conversations/(session+'.db'),rows)
            if timeout:raise ImageError('cli_timeout_unknown')
            return self.envelope(session,json.dumps(ARTICLE))
        self.behaviour=response

    def receipt_parts(self):
        receipt=dict(self.ask.last_receipt)
        job=Path(self.cfg.queue_dir)/receipt['job_id']
        return (receipt,json.loads((job/'request.json').read_bytes()),(job/'output.json').read_bytes(),
                (job/'native-final.bin').read_bytes(),(job/'native-user.bin').read_bytes(),
                (job/'native-transcript.json').read_bytes())

    def verify(self,parts):
        receipt,request,output,final,user,proof=parts
        return verify_text_receipt(self.cfg,receipt,request,output,final,user,transcript=proof)

    def test_nudged_worker_producer_roundtrip_binds_complete_native_proof_without_redispatch(self):
        self.configure_nudges()
        self.assertEqual(self.submit(),ARTICLE)
        parts=self.receipt_parts()
        self.assertEqual(self.verify(parts),ARTICLE)
        self.assertEqual(parts[0]['native_transcript_sha'],hashlib.sha256(parts[-1]).hexdigest())
        self.assertEqual(len(json.loads(parts[-1])),8)
        self.assertEqual(self.submit(),ARTICLE)
        self.assertEqual(len(self.calls),1)

    def test_timeout_with_proven_identical_nudges_salvages_without_another_dispatch(self):
        self.configure_nudges(timeout=True)
        self.assertEqual(self.submit(),ARTICLE)
        self.assertEqual(self.ask.last_receipt['status'],'complete_salvaged')
        self.assertEqual(self.verify(self.receipt_parts()),ARTICLE)
        self.assertEqual(self.submit(),ARTICLE)
        self.assertEqual(len(self.calls),1)

    def test_changed_intermediate_answer_is_accepted_final_wins_and_never_redispatches(self):
        self.configure_nudges(change=True)
        for _ in range(2):self.assertEqual(self.submit(),ARTICLE)
        self.assertEqual(self.ask.last_receipt['answers_distinct'],2)
        self.assertEqual(len(self.calls),1)
        with Directory(self.cfg.queue_dir) as queue:self.assertIsNone(self.worker.scan(queue,self.private))

    def test_receipt_rejects_missing_proof_or_forged_steps_even_with_valid_first_and_final_records(self):
        self.configure_nudges();self.submit();good=self.receipt_parts()
        variants=[]
        missing=list(good);missing[-1]=None;variants.append(missing)
        stripped=list(good);stripped[0]=dict(good[0]);stripped[0].pop('native_transcript_sha');stripped[-1]=None;variants.append(stripped)
        forged=list(good);forged[0]=dict(good[0]);forged[0]['native_steps']=[14,15];variants.append(forged)
        for parts in variants:
            with self.assertRaises(ImageError):self.verify(parts)

    def test_producer_revalidates_all_proof_rows_even_when_attacker_rehashes_modified_proof(self):
        self.configure_nudges();self.submit();good=self.receipt_parts()
        for index,payload in ((2,nudge_record('unknown instruction')),
                              (3,assistant_record(json.dumps({'title':'Changed'}))),
                              (3,assistant_record(json.dumps(ARTICLE),tool=True))):
            parts=list(good);parts[0]=dict(good[0]);rows=json.loads(good[-1]);rows[index][4]=payload.hex()
            parts[-1]=json.dumps(rows).encode();parts[0]['native_transcript_sha']=hashlib.sha256(parts[-1]).hexdigest()
            with self.subTest(index=index),self.assertRaises(ImageError):self.verify(parts)

    def test_joint_hash_and_step_stripping_cannot_hide_malicious_intermediate_rows(self):
        self.configure_nudges();self.submit();good=self.receipt_parts()
        malicious=json.loads(good[-1]);malicious[2][4]=nudge_record('malicious intermediate instruction').hex()
        for proof in (None,json.dumps(malicious).encode()):
            parts=list(good);parts[0]=dict(good[0])
            parts[0].pop('native_transcript_sha');parts[0]['native_steps']=[14,15];parts[-1]=proof
            with self.subTest(proof_present=proof is not None),self.assertRaises(ImageError):self.verify(parts)

    def assert_producer_joint_downgrade_fails(self, *, timeout):
        self.configure_nudges(timeout=timeout);self.submit()
        receipt,_,_,_,_,proof=self.receipt_parts()
        self.assertEqual(receipt['status'],'complete_salvaged' if timeout else 'complete')
        job=Path(self.cfg.queue_dir)/receipt['job_id']
        rows=json.loads(proof);rows[2][4]=nudge_record('malicious intermediate instruction').hex()
        (job/'native-transcript.json').write_text(json.dumps(rows),encoding='utf-8')
        receipt.pop('native_transcript_sha');receipt['native_steps']=[14,15]
        (job/'receipt.json').write_text(json.dumps(receipt),encoding='utf-8')
        for _ in range(2):
            with self.assertRaises(AgyTextError) as raised:self.submit()
            self.assertEqual((raised.exception.code,raised.exception.detail),('failed','cli_receipt_invalid'))
        self.assertEqual(len(self.calls),1)

    def test_complete_producer_rejects_joint_downgrade_with_existing_malicious_proof(self):
        self.assert_producer_joint_downgrade_fails(timeout=False)

    def test_salvaged_producer_rejects_joint_downgrade_with_existing_malicious_proof(self):
        self.assert_producer_joint_downgrade_fails(timeout=True)

    def test_old_complete_receipt_without_proof_stays_attached_failed_and_never_regenerates(self):
        self.submit();parts=list(self.receipt_parts());receipt=dict(parts[0])
        parts[0].pop('native_transcript_sha');parts[-1]=None
        with self.assertRaises(ImageError):self.verify(parts)
        job=Path(self.cfg.queue_dir)/receipt['job_id']
        receipt.pop('native_transcript_sha')
        (job/'receipt.json').write_text(json.dumps(receipt),encoding='utf-8')
        (job/'native-transcript.json').unlink()
        before=sorted(p.name for p in Path(self.cfg.queue_dir).iterdir() if p.is_dir())
        for offset in (0,86400):
            self.clock.t+=offset
            with self.assertRaises(AgyTextError) as raised:self.submit()
            self.assertEqual((raised.exception.code,raised.exception.detail),('failed','cli_receipt_invalid'))
        self.assertEqual(sorted(p.name for p in Path(self.cfg.queue_dir).iterdir() if p.is_dir()),before)
        self.assertEqual(len(self.calls),1)
        self.assertEqual(json.loads((job/'receipt.json').read_bytes()),receipt)
        with Directory(self.cfg.queue_dir) as queue:self.assertIsNone(self.worker.scan(queue,self.private))


if __name__=='__main__':unittest.main()
