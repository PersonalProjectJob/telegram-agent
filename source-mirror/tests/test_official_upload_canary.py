from pathlib import Path
import tempfile
import unittest
from source_mirror.official_upload_canary import isolated_profile

class ProfileTests(unittest.TestCase):
    def test_private_copy_does_not_modify_original_and_is_removed(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root); original=root/'browser'/'nailhub-profile'; original.mkdir(parents=True)
            (original/'preferences').write_text('original')
            (original/'Cache').mkdir(); (original/'Cache'/'cache').write_text('ignored')
            with isolated_profile(root) as copied:
                self.assertEqual((copied/'preferences').read_text(),'original')
                self.assertFalse((copied/'Cache').exists())
                (copied/'preferences').write_text('changed'); path=copied
            self.assertFalse(path.exists()); self.assertEqual((original/'preferences').read_text(),'original')

    def test_symlinked_profile_content_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root); original=root/'browser'/'nailhub-profile'; original.mkdir(parents=True)
            try: (original/'linked').symlink_to(root,target_is_directory=True)
            except OSError: self.skipTest('symlink creation unavailable')
            with self.assertRaisesRegex(ValueError,'symlink'):
                with isolated_profile(root): pass

    def test_leftover_profile_blocks_without_deleting_or_touching_live_profile(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root); original=root/'browser'/'nailhub-profile'; original.mkdir(parents=True)
            (original/'preferences').write_text('original')
            leftover=root/'tmp'/'official-canary-crashed'; leftover.mkdir(parents=True)
            (leftover/'private-state').write_text('retained')
            with self.assertRaisesRegex(ValueError,'requires reconciliation'):
                with isolated_profile(root): pass
            self.assertEqual((leftover/'private-state').read_text(),'retained')
            self.assertEqual((original/'preferences').read_text(),'original')
