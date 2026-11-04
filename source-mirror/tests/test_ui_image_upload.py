import unittest
from unittest.mock import Mock
from source_mirror.ui_image_upload import upload_with_chooser


class Context:
    def __init__(self,value): self.value=value
    def __enter__(self): return self
    def __exit__(self,*args): return False


class UploadTests(unittest.TestCase):
    def test_both_controls_use_chooser_and_caller_response_binding(self):
        for button in ('Upload Cover Image','Upload Images'):
            chooser=Mock(); response=Mock(); page=Mock(); dialog=Mock()
            page.expect_file_chooser.return_value=Context(chooser)
            page.expect_response.return_value=Context(response)
            predicate=lambda r:r is response
            files={'name':'x.png','mimeType':'image/png','buffer':b'bytes'}
            self.assertIs(upload_with_chooser(page,dialog,button,files,predicate),response)
            dialog.get_by_role.assert_called_once_with('button',name=button,exact=True)
            chooser.set_files.assert_called_once_with(files)
            page.expect_response.assert_called_once_with(predicate,timeout=30000)

    def test_unexpected_control_or_expectation_fails_before_ui(self):
        page=Mock(); dialog=Mock()
        with self.assertRaises(ValueError): upload_with_chooser(page,dialog,'Other','x',lambda _:True)
        with self.assertRaises(ValueError): upload_with_chooser(page,dialog,'Upload Images','x',None)
        page.expect_file_chooser.assert_not_called()
