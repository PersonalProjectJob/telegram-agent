"""Bounded remote WebP decoding keeps CLI and historical API cover contracts separate."""
from dataclasses import replace
import hashlib
import io
import unittest
from unittest.mock import Mock,patch
from PIL import Image

from nailblog import admin
from nailblog.config import ContractConfig,ImageConfig
from nailblog.publication import AdminError,STAGING_API_ORIGIN,STAGING_SITE_ORIGIN,STAGING_MEDIA_ORIGIN
from tests.helpers import settings


class RemoteCoverTests(unittest.TestCase):
    def test_provider_specific_remote_cover_dimensions_and_hashes(self):
        contract=ContractConfig(environment='staging',api_origin=STAGING_API_ORIGIN,
            site_origin=STAGING_SITE_ORIGIN,media_origin=STAGING_MEDIA_ORIGIN)
        base=settings('/unused',contract=contract)
        for provider,size,cover,accepted in (
            ('subscription',(1376,768),True,True),('gemini',(1600,900),True,True),
            ('subscription',(1600,900),True,False),('gemini',(1376,768),True,False),
            ('subscription',(1375,768),True,False),('gemini',(1200,675),True,False),
            ('subscription',(1200,675),False,True),('gemini',(1200,675),False,True)):
            with self.subTest(provider=provider,size=size,cover=cover):
                stream=io.BytesIO();Image.new('RGB',size,'tan').save(stream,format='WEBP');raw=stream.getvalue()
                response=Mock(status=200)
                response.getheader.side_effect=lambda key: {'Content-Type':'image/webp','Content-Length':str(len(raw))}.get(key)
                response.read1=io.BytesIO(raw).read1
                connection=Mock();connection.getresponse.return_value=response
                cfg=replace(base,image=ImageConfig(provider=provider))
                with patch.object(admin,'resolve',return_value=[(None,None,None,None,('8.8.8.8',443))]), \
                     patch.object(admin,'PinnedHTTPS',return_value=connection),patch.object(admin.threading,'Timer'):
                    call=lambda:admin.public_webp(cfg,STAGING_MEDIA_ORIGIN+'/cover.webp',cover=cover,
                                                 approved_sha=hashlib.sha256(raw).hexdigest())
                    if accepted:
                        result=call()
                        self.assertEqual(result['dimensions'],list(size))
                        self.assertEqual(result['sha256'],hashlib.sha256(raw).hexdigest())
                        self.assertTrue(result['approved_sha_verified'])
                    else:
                        with self.assertRaisesRegex(AdminError,'published_image_format_or_dimensions_invalid'):call()
                connection.request.assert_called_once()
                response.close.assert_called_once();connection.close.assert_called_once()

