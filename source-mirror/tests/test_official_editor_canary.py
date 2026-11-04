import unittest
from source_mirror.official_editor_canary import allowed_read

class CanaryRouteTests(unittest.TestCase):
    def test_exact_read_paths_only(self):
        for url in ('https://nailhub.ai/admin/blogs','https://nailhub.ai/assets/app-123.js',
                    'https://api.nailhub.ai/api/v1/UserProfile/me','https://api.nailhub.ai/api/v1/admin/blog-categories',
                    'https://api.nailhub.ai/api/v1/admin/blogs?pageNumber=1'):
            with self.subTest(url=url): self.assertTrue(allowed_read('GET',url))
        for method,url in (('POST','https://api.nailhub.ai/api/v1/admin/blogs'),
                           ('POST','https://api.nailhub.ai/api/v1/Images/quick-upload'),
                           ('OPTIONS','https://api.nailhub.ai/api/v1/admin/blogs'),
                           ('GET','https://api.nailhub.ai/api/v1/admin/users'),
                           ('GET','https://evil.example/assets/app.js'),
                           ('GET','https://nailhub.ai/assets/../private.js'),
                           ('GET','https://nailhub.ai/assets/%2e%2e/private.js'),
                           ('GET','https://user@nailhub.ai/admin/blogs')):
            with self.subTest(url=url,method=method): self.assertFalse(allowed_read(method,url))
