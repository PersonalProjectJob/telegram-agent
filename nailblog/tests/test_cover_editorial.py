import unittest

from PIL import Image

from nailblog.images import ImageError
from nailblog.images.post import thumbnail_cli
from nailblog.images.prompts import generation_prompt


class CoverEditorialTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_cover_preserves_pixels_and_rejects_wrong_size(self):
        image = Image.new('RGB', (1376, 768), (220, 170, 120))
        result = await thumbnail_cli(image, 'A title that must not appear')
        self.assertEqual(result.tobytes(), image.tobytes())
        with self.assertRaisesRegex(ImageError, 'cli_cover_dimensions_changed'):
            await thumbnail_cli(Image.new('RGB', (1200, 675)), 'title')

    def test_cover_prompt_requests_bright_topic_matched_image_without_overlay(self):
        for subject in ('nail_editorial', 'nail_swatch_sticks'):
            prompt = generation_prompt('A coral palette with a glossy finish', 0, subject)
            self.assertIn('bright, vivid', prompt)
            self.assertIn('closely matched to the article subject', prompt)
            self.assertIn('No dark overlay', prompt)
            self.assertIn('A coral palette with a glossy finish', prompt)
            self.assertNotIn('later title overlay', prompt)
