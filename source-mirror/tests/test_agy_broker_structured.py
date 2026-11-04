import copy
import json
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from source_mirror import agy_broker as broker
from source_mirror.agy_structured import schema
from source_mirror.localization import validate_review


class BrokerStructuredTests(unittest.TestCase):
    def setUp(self):
        self.config={'agy_binary':sys.executable,'workdir':tempfile.gettempdir()}
        self.review={'verdict':'HOLD','critical':0,'major':0,'minor':0,'issues':[],
                     'uncertainties':['Source ambiguity'],'glossary_ok':True,
                     'accuracy_ok':False,'technical_clarity_ok':False}
        self.draft={'title_vi':'Bản thử','text_vi':'Nội dung.','uncertainties':['Unclear term']}

    def envelope(self,kind):
        value=copy.deepcopy(self.draft if kind=='official-draft' else self.review)
        return {'status':'SUCCESS','response':json.dumps(value),'structured_output':value,'json_schema':schema(kind)}

    def run_infer(self,request,envelope):
        with patch.object(broker.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(envelope))) as run:
            result=broker.infer(self.config,request)
        return result,run.call_args

    def test_legacy_prompt_packet_keeps_original_cli_and_raw_response(self):
        original={'status':'SUCCESS','response':'```json\n{}\n```','extra_legacy_metadata':'unchanged'}
        result,(args,kwargs)=self.run_infer({'prompt':'official-draft mqm-review'},original)
        self.assertEqual(result,original)
        self.assertEqual(args[0],[sys.executable,'--model',broker.MODEL,'--effort',broker.EFFORT,
            '--sandbox','--disable-slash-commands','--output-format','json','--print-timeout','180s',
            '--print','official-draft mqm-review'])
        self.assertNotIn('shell',kwargs)

    def test_distinct_server_schemas_fixed_execution_and_original_evidence(self):
        for kind in ('official-draft','mqm-review'):
            with self.subTest(kind=kind):
                original=self.envelope(kind)
                result,(args,kwargs)=self.run_infer({'prompt':'untrusted text','structured_kind':kind},original)
                argv=args[0]
                self.assertEqual(json.loads(argv[argv.index('--json-schema')+1]),schema(kind))
                self.assertEqual(argv[:9],[sys.executable,'--model',broker.MODEL,'--effort',broker.EFFORT,
                    '--sandbox','--disable-slash-commands','--output-format','json'])
                self.assertEqual(kwargs,{'cwd':self.config['workdir'],'capture_output':True,'text':True,'timeout':200,'check':False})
                self.assertEqual(result['original_envelope'],original)
                self.assertEqual(result['provider_response'],original['response'])
                self.assertIs(result['publication_enabled'],False)
                self.assertEqual(json.loads(result['response']),original['structured_output'])
                if kind=='mqm-review':
                    with self.assertRaises(ValueError):validate_review(json.loads(result['response']))
        self.assertNotEqual(schema('official-draft'),schema('mqm-review'))

    def test_request_allowlist_rejects_before_execution(self):
        invalid=[{'prompt':'x','structured_kind':kind} for kind in (None,False,{},'factual','--model other')]
        invalid += [{'prompt':'x','schema':{}},{'prompt':'x','model':'other'},
                    {'prompt':'x','structured_kind':'mqm-review','schema':schema('mqm-review')},
                    {'prompt':''},{'prompt':'x'*150001}]
        with patch.object(broker.subprocess,'run') as run:
            for request in invalid:
                with self.subTest(request=str(request)[:80]),self.assertRaises(ValueError):broker.infer(self.config,request)
            run.assert_not_called()

    def test_structured_envelope_mismatch_warning_and_duplicate_fail_closed(self):
        for change in ({'structured_output':{}},{'json_schema':schema('official-draft')},
                       {'response':'Warning remains'}, {'warning':'do not ignore'}, {'status':'ERROR'}):
            envelope=self.envelope('mqm-review');envelope.update(change)
            with self.subTest(change=change),self.assertRaises(ValueError):
                self.run_infer({'prompt':'x','structured_kind':'mqm-review'},envelope)
        raw=json.dumps(self.envelope('mqm-review'))[:-1]+',"status":"SUCCESS"}'
        with patch.object(broker.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=raw)):
            with self.assertRaises(ValueError):broker.infer(self.config,{'prompt':'x','structured_kind':'mqm-review'})

    def test_client_legacy_and_opt_in_packets(self):
        for kind in (None,'official-draft','mqm-review'):
            connection=MagicMock();connection.__enter__.return_value=connection
            with patch.object(broker.socket,'AF_UNIX',1,create=True),patch.object(broker.socket,'socket',return_value=connection),patch.object(broker,'receive',return_value={'ok':True,'result':{'receipt':1}}):
                self.assertEqual(broker.call('/private/agy.sock','x',structured_kind=kind),{'receipt':1})
            packet=json.loads(connection.sendall.call_args.args[0])
            expected={'prompt':'x'}
            if kind is not None:expected['structured_kind']=kind
            self.assertEqual(packet,expected)
            connection.settimeout.assert_called_once_with(220)
        with patch.object(broker.socket,'socket') as socket_factory:
            with self.assertRaises(ValueError):broker.call('unused','x',structured_kind='arbitrary')
            with self.assertRaises(ValueError):broker.call('unused','x'*broker.MAX_MESSAGE)
            socket_factory.assert_not_called()

    def test_serialized_reply_cap_applies_to_expanded_evidence(self):
        normal={'ok':True,'result':self.envelope('mqm-review')}
        self.assertEqual(json.loads(broker.encode_reply(normal)),normal)
        oversized=broker.encode_reply({'ok':True,'result':{'original_envelope':'x'*broker.MAX_MESSAGE}})
        self.assertLessEqual(len(oversized),broker.MAX_MESSAGE)
        self.assertEqual(json.loads(oversized),{'ok':False,'error':'ResponseTooLarge'})

    def test_provider_timeout_failure_and_oversize_remain_fail_closed(self):
        request={'prompt':'x','structured_kind':'mqm-review'}
        with patch.object(broker.subprocess,'run',side_effect=broker.subprocess.TimeoutExpired('agy',200)):
            with self.assertRaises(broker.subprocess.TimeoutExpired):broker.infer(self.config,request)
        for result in (SimpleNamespace(returncode=1,stdout=''),
                       SimpleNamespace(returncode=0,stdout='x'*(broker.MAX_MESSAGE+1))):
            with patch.object(broker.subprocess,'run',return_value=result):
                with self.assertRaises(RuntimeError):broker.infer(self.config,request)

    def test_unauthorized_peer_cannot_submit_structured_request(self):
        with tempfile.TemporaryDirectory() as directory:
            connection=MagicMock();connection.__enter__.return_value=connection
            connection.getsockopt.return_value=struct.pack('3i',1,123,1)
            listener=MagicMock();listener.__enter__.return_value=listener
            listener.accept.side_effect=[(connection,None),StopIteration]
            config=dict(self.config,socket_path=str(Path(directory)/'agy.sock'),allowed_uid=991)
            with patch.object(broker.os,'geteuid',return_value=990,create=True),patch.object(broker.os,'chmod'),\
                 patch.object(broker.socket,'SO_PEERCRED',17,create=True),patch.object(broker.socket,'AF_UNIX',1,create=True),patch.object(broker.socket,'socket',return_value=listener),\
                 patch.object(broker,'receive') as receive,patch.object(broker,'infer') as infer:
                with self.assertRaises(StopIteration):broker.serve(config)
                receive.assert_not_called();infer.assert_not_called()
            self.assertEqual(json.loads(connection.sendall.call_args.args[0]),{'ok':False,'error':'PermissionError'})


if __name__=='__main__':unittest.main()
