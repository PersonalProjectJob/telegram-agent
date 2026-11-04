import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from source_mirror.official_direct_upload import upload


class DirectUploadTests(unittest.TestCase):
    def test_exact_media_is_chosen_and_duplicate_requires_reconciliation(self):
        raw=b'\xff\xd8\xfffixture'; digest=hashlib.sha256(raw).hexdigest()
        payload=json.dumps({'media':[{'id':'one','sha256':digest}]}).encode()
        evidence=(payload,{'status':'REVALIDATED_NOT_PUBLISHABLE'},{},{'one':raw},{})
        response=Mock(); response.headers={}
        data={'imageUrl':'https://vlink-storages-prod.sgp1.cdn.digitaloceanspaces.com/x.jpg','imageRelativePath':'/x.jpg','thumbnailUrl':'','thumbnailRelativePath':''}
        response.body.return_value=json.dumps(data).encode()
        response.url='https://api.nailhub.ai/api/v1/Images/quick-upload'; response.ok=True; response.request.method='POST'
        response.request.headers={'content-type':'multipart/form-data; boundary=BOUND'}
        response.request.post_data_buffer=(b'--BOUND\r\nContent-Disposition: form-data; name="file"; filename="x.jpg"\r\nContent-Type: image/jpeg\r\n\r\n'+raw+b'\r\n--BOUND--\r\n')
        with tempfile.TemporaryDirectory() as root:
            folder=Path(root)/'evidence';folder.mkdir()
            with patch('source_mirror.official_direct_upload._live_inputs',return_value=evidence), \
                 patch('source_mirror.official_direct_upload._sync_directory_chain'), \
                 patch('source_mirror.official_direct_upload.upload_with_chooser',return_value=response) as chooser:
                result=upload(root,folder,'a'*64,Mock(),Mock(),'one','cover')
                self.assertEqual(result['status'],'UPLOADED_NOT_PUBLISHABLE')
                self.assertEqual(chooser.call_args.args[2],'Upload Cover Image')
                self.assertEqual(chooser.call_args.args[3]['buffer'],raw)
                predicate=chooser.call_args.args[4]
                self.assertTrue(predicate(response))
                competing=Mock();competing.url=response.url;competing.ok=True;competing.request.method='POST';competing.request.headers=response.request.headers;competing.request.post_data_buffer=response.request.post_data_buffer.replace(raw,b'\xff\xd8\xffother')
                self.assertFalse(predicate(competing))
                self.assertTrue(list((folder/'direct-upload-receipts').glob('*.json')))
                self.assertEqual(next((folder/'direct-upload-responses').glob('*.json')).read_bytes(),response.body.return_value)
                with self.assertRaises(FileExistsError):upload(root,folder,'a'*64,Mock(),Mock(),'one','cover')
                self.assertEqual(chooser.call_count,1)

    def test_mismatched_media_blocks_before_ui(self):
        payload=json.dumps({'media':[{'id':'one','sha256':'0'*64}]}).encode()
        evidence=(payload,{'status':'REVALIDATED_NOT_PUBLISHABLE'},{},{'one':b'\xff\xd8\xffx'},{})
        with patch('source_mirror.official_direct_upload._live_inputs',return_value=evidence), \
             patch('source_mirror.official_direct_upload.upload_with_chooser') as chooser:
            with self.assertRaisesRegex(ValueError,'binding mismatch'):upload('/root','/unused','a'*64,Mock(),Mock(),'one','gallery')
            chooser.assert_not_called()

    def test_oversized_response_keeps_intent_and_is_never_retried(self):
        raw=b'\xff\xd8\xfffixture'; digest=hashlib.sha256(raw).hexdigest()
        payload=json.dumps({'media':[{'id':'one','sha256':digest}]}).encode()
        evidence=(payload,{'status':'REVALIDATED_NOT_PUBLISHABLE'},{},{'one':raw},{})
        response=Mock();response.headers={'content-length':'65537'}
        with tempfile.TemporaryDirectory() as root:
            folder=Path(root)/'evidence';folder.mkdir()
            with patch('source_mirror.official_direct_upload._live_inputs',return_value=evidence), \
                 patch('source_mirror.official_direct_upload._sync_directory_chain'), \
                 patch('source_mirror.official_direct_upload.upload_with_chooser',return_value=response) as chooser:
                with self.assertRaisesRegex(ValueError,'size unavailable'):upload(root,folder,'a'*64,Mock(),Mock(),'one','gallery')
                response.body.assert_not_called()
                with self.assertRaises(FileExistsError):upload(root,folder,'a'*64,Mock(),Mock(),'one','gallery')
                self.assertEqual(chooser.call_count,1)

    def test_understated_response_is_rejected_after_read(self):
        raw=b'\xff\xd8\xfffixture'; digest=hashlib.sha256(raw).hexdigest()
        payload=json.dumps({'media':[{'id':'one','sha256':digest}]}).encode()
        evidence=(payload,{'status':'REVALIDATED_NOT_PUBLISHABLE'},{},{'one':raw},{})
        response=Mock();response.headers={'content-length':'1'};response.body.return_value=b'x'*65537
        with tempfile.TemporaryDirectory() as root:
            folder=Path(root)/'evidence';folder.mkdir()
            with patch('source_mirror.official_direct_upload._live_inputs',return_value=evidence), \
                 patch('source_mirror.official_direct_upload._sync_directory_chain'), \
                 patch('source_mirror.official_direct_upload.upload_with_chooser',return_value=response):
                with self.assertRaisesRegex(ValueError,'too large'):upload(root,folder,'a'*64,Mock(),Mock(),'one','gallery')

    def test_invalid_response_retained_privately_for_reconciliation(self):
        raw=b'\xff\xd8\xfffixture'; digest=hashlib.sha256(raw).hexdigest()
        payload=json.dumps({'media':[{'id':'one','sha256':digest}]}).encode()
        evidence=(payload,{'status':'REVALIDATED_NOT_PUBLISHABLE'},{},{'one':raw},{})
        response=Mock(); response.headers={};response.body.return_value=b'{"unexpected":true}'
        with tempfile.TemporaryDirectory() as root:
            folder=Path(root)/'evidence';folder.mkdir()
            with patch('source_mirror.official_direct_upload._live_inputs',return_value=evidence), \
                 patch('source_mirror.official_direct_upload._sync_directory_chain'), \
                 patch('source_mirror.official_direct_upload.upload_with_chooser',return_value=response):
                with self.assertRaises(ValueError):upload(root,folder,'a'*64,Mock(),Mock(),'one','gallery')
                saved=next((folder/'direct-upload-responses').glob('*.json'))
                self.assertEqual(saved.read_bytes(),response.body.return_value)
                if __import__('os').name != 'nt':self.assertEqual(saved.stat().st_mode & 0o777,0o600)
                self.assertFalse((folder/'direct-upload-receipts').exists())
