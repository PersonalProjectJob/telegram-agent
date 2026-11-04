import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock,patch
from source_mirror.official_upload_controller import UploadObserver, _sync_directory_chain

class UploadControllerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.folder=self.root/'evidence'; self.folder.mkdir()
        self.image=b'\xff\xd8\xffsource image'; self.request=MagicMock()
        self.request.url='https://api.nailhub.ai/api/v1/Images/quick-upload'; self.request.method='POST'
        self.request.headers={'content-type':'multipart/form-data; boundary=abc'}
        self.request.post_data_buffer=(b'--abc\r\nContent-Disposition: form-data; name="file"; filename="x.jpg"\r\n'
                                       b'Content-Type: image/jpeg\r\n\r\n'+self.image+b'\r\n--abc--\r\n')
        self.route=MagicMock(); self.route.request=self.request
        self.values=(b'payload',{'status':'REVALIDATED_NOT_PUBLISHABLE'},{'category':'id'}, {'photo':self.image},{'version':'trusted'})
        self.patcher=patch('source_mirror.official_upload_controller._live_inputs',return_value=self.values)
        self.mock_live=self.patcher.start(); self.addCleanup(self.patcher.stop)
        if os.name=='nt':
            sync_patcher=patch('source_mirror.official_upload_controller._sync_directory_chain')
            sync_patcher.start(); self.addCleanup(sync_patcher.stop)
        self.observer=UploadObserver(self.root,self.folder,'a'*64)
        self.receipt={'imageUrl':'https://vlink-storages-prod.sgp1.cdn.digitaloceanspaces.com/a.jpg','imageRelativePath':'/a.jpg','thumbnailUrl':'','thumbnailRelativePath':''}

    def test_real_request_object_response_is_bound_but_never_authorizes_publish(self):
        self.observer.arm('photo'); self.observer.route(self.route); self.route.continue_.assert_called_once()
        body=json.dumps(self.receipt).encode(); response=MagicMock(); response.request=self.request; response.status=200
        response.headers={'content-length':str(len(body))}; response.body.return_value=body
        result=self.observer.response(response); self.assertEqual(result['status'],'CANARY_ONLY_NOT_PUBLISHABLE')
        recorded=Path(result['path']).read_bytes(); self.assertEqual(hashlib.sha256(recorded).hexdigest(),result['observation_sha256'])
        self.assertEqual(json.loads(recorded)['binding']['source_sha256'],hashlib.sha256(self.image).hexdigest())
        with self.assertRaises(ValueError): self.observer.arm('photo')
        restarted=UploadObserver(self.root,self.folder,'a'*64)
        with self.assertRaisesRegex(ValueError,'requires reconciliation'): restarted.arm('photo')

    def test_unarmed_or_tampered_request_aborts_before_network(self):
        with self.assertRaises(ValueError): self.observer.route(self.route)
        self.route.abort.assert_called_once(); self.route.continue_.assert_not_called()
        other=UploadObserver(self.root,self.folder,'a'*64); other.arm('photo'); self.request.post_data_buffer+=b'x'
        # Alter the actual file bytes, rather than an ignorable trailing boundary.
        self.request.post_data_buffer=self.request.post_data_buffer.replace(self.image,b'\xff\xd8\xffaltered')
        self.route.reset_mock()
        with self.assertRaises(ValueError): other.route(self.route)
        self.route.abort.assert_called_once(); self.route.continue_.assert_not_called()

    def test_other_response_and_changed_trusted_state_are_held(self):
        self.observer.arm('photo'); self.observer.route(self.route)
        response=MagicMock(); response.request=MagicMock(); response.status=200
        with self.assertRaisesRegex(ValueError,'does not match'): self.observer.response(response)
        self.assertFalse((self.folder/'upload-observations').exists())
        restarted=UploadObserver(self.root,self.folder,'a'*64)
        with self.assertRaisesRegex(ValueError,'requires reconciliation'): restarted.arm('photo')
        other=UploadObserver(self.root,self.folder,'a'*64); self.mock_live.return_value=(b'changed',*self.values[1:])
        with self.assertRaisesRegex(ValueError,'changed before upload'): other.arm('photo')

    def test_pending_request_identity_is_required_before_bounded_read(self):
        self.observer.arm('photo');self.observer.route(self.route)
        response=MagicMock();response.request=MagicMock();response.status=200;reader=MagicMock()
        with self.assertRaisesRegex(ValueError,'does not match'):self.observer.response(response,bounded_reader=reader)
        reader.read.assert_not_called();response.body.assert_not_called()

    def test_bounded_reader_does_not_use_untrusted_size_header_or_fallback_body(self):
        self.observer.arm('photo');self.observer.route(self.route)
        response=MagicMock();response.request=self.request;response.status=200
        response.headers={'content-length':'999999999'};reader=MagicMock();reader.read.return_value=json.dumps(self.receipt).encode()
        value=self.observer.response(response,bounded_reader=reader)
        self.assertEqual(value['status'],'CANARY_ONLY_NOT_PUBLISHABLE');response.body.assert_not_called()

    def test_fsync_fault_before_send_aborts_and_never_retries_possible_intent(self):
        self.observer.arm('photo')
        with patch('source_mirror.official_upload_controller._sync_directory_chain',side_effect=[None,OSError('sync failed')]):
            with self.assertRaises(OSError): self.observer.route(self.route)
        self.route.abort.assert_called_once(); self.route.continue_.assert_not_called()
        restarted=UploadObserver(self.root,self.folder,'a'*64)
        with self.assertRaisesRegex(ValueError,'requires reconciliation'): restarted.arm('photo')

    def test_parent_chain_sync_failure_prevents_send_before_intent(self):
        self.observer.arm('photo')
        with patch('source_mirror.official_upload_controller._sync_directory_chain',side_effect=OSError('sync failed')):
            with self.assertRaises(OSError): self.observer.route(self.route)
        self.route.abort.assert_called_once(); self.route.continue_.assert_not_called()

    @unittest.skipIf(os.name=='nt','directory fsync requires POSIX')
    def test_directory_sync_covers_intent_parent_root_and_root_parent(self):
        directory=self.folder/'upload-intents'; directory.mkdir()
        with patch('source_mirror.official_upload_controller.os.open',return_value=77) as opened, \
             patch('source_mirror.official_upload_controller.os.fsync') as synced, \
             patch('source_mirror.official_upload_controller.os.close'):
            _sync_directory_chain(directory,self.root)
        self.assertEqual([call.args[0] for call in opened.call_args_list],[directory,self.folder,self.root,self.root.parent])
        self.assertEqual(synced.call_count,4)

    @unittest.skipUnless(os.name=='nt','Windows-specific fail-closed behavior')
    def test_windows_directory_sync_fails_closed(self):
        with self.assertRaisesRegex(ValueError,'unavailable on Windows'):
            _sync_directory_chain(self.folder,self.root)
