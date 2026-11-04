import copy
import hashlib
import json
from pathlib import Path
import stat
import struct
import tempfile
import unittest
from unittest.mock import MagicMock,patch

from source_mirror.agy_structured import normalize,schema
from source_mirror.official_draft import create,output_instruction
from source_mirror.official_payload import build
from source_mirror.official_sources import collect,SOURCES
from source_mirror.official_auto_eligibility import trusted_broker_call
from source_mirror.localization import QualityHold
from tests.test_official_draft import HTML,CONFIG,CANDIDATE,REVIEW


def wrapped(value,kind):
    return normalize({'status':'SUCCESS','response':json.dumps(value),'structured_output':value,'json_schema':schema(kind)},kind)


class OfficialStructuredRouteTests(unittest.TestCase):
    def test_stage_binding_evidence_and_existing_quality_gate(self):
        with tempfile.TemporaryDirectory() as root:
            collect(root,SOURCES[0],lambda _:HTML);sha=hashlib.sha256(HTML).hexdigest();calls=[]
            def infer(prompt,kind):
                calls.append(kind)
                self.assertIn('native schema finalization mechanism',prompt)
                self.assertNotIn('not as a tool call or file',prompt)
                self.assertIn('Do not emit preliminary plaintext JSON',prompt)
                return wrapped(CANDIDATE if kind=='official-draft' else REVIEW,kind)
            result=create(root,SOURCES[0]['id'],sha,CONFIG,structured_inference=infer)
            artifact=json.loads(Path(result['path']).read_text(encoding='utf-8'))
            self.assertEqual(calls,['official-draft','mqm-review'])
            self.assertEqual(artifact['status'],'HOLD')
            for field,kind in [('generation_envelope','official-draft'),('review_envelope','mqm-review')]:
                self.assertEqual(artifact[field]['original_envelope']['json_schema'],schema(kind))
                altered=copy.deepcopy(artifact);altered[field]['provider_response']='tampered'
                with self.assertRaisesRegex(ValueError,'structured draft evidence'):
                    build(json.dumps(altered,ensure_ascii=False).encode(),[],{})
            def held(prompt,kind):
                return wrapped(dict(CANDIDATE,uncertainties=['meaning unclear']) if kind=='official-draft' else REVIEW,kind)
            with self.assertRaises(QualityHold):create(root,SOURCES[0]['id'],sha,CONFIG,structured_inference=held)
            def false_pass(prompt,kind):
                return wrapped(CANDIDATE if kind=='official-draft' else dict(REVIEW,major=1),kind)
            with self.assertRaises(ValueError):create(root,SOURCES[0]['id'],sha,CONFIG,structured_inference=false_pass)
            def tamper(prompt,kind):
                value=wrapped(CANDIDATE,kind);value['provider_response']='altered';return value
            with self.assertRaises(QualityHold):create(root,SOURCES[0]['id'],sha,CONFIG,structured_inference=tamper)
            with self.assertRaisesRegex(QualityHold,'retry forbidden'):
                create(root,SOURCES[0]['id'],sha,CONFIG,structured_inference=lambda *args:self.fail('retry sent'),max_generation_attempts=2)

    def test_legacy_prefix_unchanged_and_structured_disagreement_is_not_repaired(self):
        self.assertEqual(output_instruction('transformation'),
            'This is a text-only transformation. Do not call tools, execute commands, access the network, '
            'or read/write files. Do not run Python or use a terminal to build the answer. '
            'Return the complete JSON object directly in your final response, not as a tool call or file. ')
        first=dict(CANDIDATE,text_vi=CANDIDATE['text_vi']+' (manicure)')
        envelope={'status':'SUCCESS','response':json.dumps(first),'structured_output':CANDIDATE,'json_schema':schema('official-draft')}
        with self.assertRaisesRegex(ValueError,'channels disagree'):normalize(envelope,'official-draft')

    def test_trusted_route_requires_opt_in_and_separate_peer_verified_socket(self):
        legacy={'version':'official-runtime-policy-v1','socket_path':'/run/legacy.sock','broker_uid':123}
        policy=dict(legacy,version='official-runtime-policy-v2',structured_socket_path='/run/official.sock',structured_broker_uid=456)
        path=MagicMock();path.lstat.return_value=type('S',(),{'st_mode':stat.S_IFSOCK|0o660,'st_uid':456})()
        path.parent.lstat.return_value=type('S',(),{'st_mode':stat.S_IFDIR|0o750,'st_uid':456})()
        conn=MagicMock();conn.__enter__.return_value=conn;conn.getsockopt.return_value=struct.pack('3i',1,456,1)
        result=wrapped(REVIEW,'mqm-review')
        with patch('source_mirror.official_auto_eligibility.os.name','posix'),patch('source_mirror.official_auto_eligibility.Path',return_value=path),\
             patch('source_mirror.official_auto_eligibility.socket.SO_PEERCRED',17,create=True),\
             patch('source_mirror.official_auto_eligibility.socket.AF_UNIX',1,create=True),\
             patch('source_mirror.official_auto_eligibility.socket.socket',return_value=conn),\
             patch('source_mirror.official_auto_eligibility.receive',return_value={'ok':True,'result':result}):
            with self.assertRaises(ValueError):trusted_broker_call(legacy,'x',structured_kind='mqm-review')
            with self.assertRaises(ValueError):trusted_broker_call(dict(policy,structured_socket_path=policy['socket_path']),'x',structured_kind='mqm-review')
            conn.sendall.assert_not_called()
            self.assertEqual(trusted_broker_call(policy,'x',structured_kind='mqm-review'),result)
            self.assertEqual(json.loads(conn.sendall.call_args.args[0]),{'prompt':'x','structured_kind':'mqm-review'})
            # Factual/editorial requests stay prompt-only, but use the same
            # separate UID/socket, never the scheduled worker's legacy broker.
            self.assertEqual(trusted_broker_call(policy,'factual/editorial'),result)
            self.assertEqual(json.loads(conn.sendall.call_args.args[0]),{'prompt':'factual/editorial'})
            for error,expected in [('ValueError','ValueError'),('TimeoutExpired','TimeoutExpired'),
                                   ('private provider token SENTINEL','UNCLASSIFIED'),({},'UNCLASSIFIED')]:
                with patch('source_mirror.official_auto_eligibility.receive',return_value={'ok':False,'error':error}):
                    with self.assertRaisesRegex(ValueError,'factual broker unavailable: '+expected) as caught:
                        trusted_broker_call(policy,'x',structured_kind='mqm-review')
                    self.assertNotIn('SENTINEL',str(caught.exception))
            conn.getsockopt.return_value=struct.pack('3i',1,123,1);conn.sendall.reset_mock()
            with self.assertRaises(PermissionError):trusted_broker_call(policy,'x',structured_kind='mqm-review')
            conn.sendall.assert_not_called()


if __name__=='__main__':unittest.main()
