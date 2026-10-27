"""Offline image helpers shared by other suites, deterministic image gates and the Agy-only ImagePipeline entry."""
import io
import random
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image, ImageDraw

from nailblog.images import ImageError
from nailblog.images.pipeline import ImagePipeline, artifact, slots
from nailblog.images.post import crop, decode, hashes, webp
from tests.helpers import settings


def fixture_image(seed=1,size=(1700,1000)):
    rng=random.Random(seed);small=Image.new('RGB',(18,12));draw=ImageDraw.Draw(small)
    for y in range(12):
        for x in range(18):draw.point((x,y),fill=tuple(rng.randrange(256) for _ in range(3)))
    image=small.resize(size,Image.Resampling.BICUBIC);stream=io.BytesIO();image.save(stream,format='PNG')
    return stream.getvalue()


def envelope(parts,*,finish='STOP',meta=None):
    return {'candidates':[{'finishReason':finish,'content':{'parts':parts}}],
            'usageMetadata':meta if meta is not None else {'promptTokenCount':100,'candidatesTokenCount':100,'thoughtsTokenCount':0}}


def article():
    return {'title':'Bảng màu nail dễ chọn cho khách','slug':'bang-mau-nail','summary':'Tóm tắt',
            'sections':[{'h2':f'Mục {n}','paragraphs':['Nội dung mẫu.'],
                         'image_slot':{'brief':f'Nail color swatches distinct palette {n}',
                                       'alt':'Bảng màu móng tay minh họa tư vấn khách','caption':'Màu minh họa'}} for n in range(3)]}


async def fixture_cover(image,title):return image


class PostTests(unittest.TestCase):
    def test_cropped_webp_dimensions_size_hash_and_rejection_of_upscale(self):
        data=fixture_image();image=crop(data,(1600,900));encoded=webp(image,200*1024)
        self.assertEqual(decode(encoded).size,(1600,900));self.assertLessEqual(len(encoded),200*1024)
        self.assertEqual(hashes(encoded),hashes(encoded))
        with self.assertRaisesRegex(ImageError,'resolution'):crop(fixture_image(size=(100,100)),(1600,900))

    def test_invalid_bytes_and_oversized_dimensions_rejected_before_loading(self):
        for data in (b'not an image',b'',b'x'*(16*1024*1024+1)):
            with self.assertRaises(ImageError):decode(data)
        image=Mock(format='PNG',n_frames=1,size=(10000,10000),width=10000,height=10000)
        with patch('nailblog.images.post.Image.open',return_value=image):
            with self.assertRaises(ImageError):decode(b'fake')
        image.load.assert_not_called()

    def test_alt_and_slot_count_preflight(self):
        self.assertEqual(len(slots(article())),4)
        bad=article();bad['sections'][0]['image_slot']['alt']='ngắn'
        with self.assertRaises(ImageError):slots(bad)
        with self.assertRaises(ImageError):slots({**article(),'sections':[]})

    def test_artifact_path_cannot_escape_output_directory(self):
        for name in ('../private.webp','/private.webp','a/b.webp','x\\a.webp','a.webp:secret'):
            with self.assertRaises(ImageError):artifact(Path('.'),name)

    def test_json_writer_rejects_destination_or_temporary_symlink_before_write(self):
        from nailblog.images.pipeline import write_atomic
        with tempfile.TemporaryDirectory() as d:
            target=Path(d)/'document.json'
            for suffix in ('.json','.tmp'):
                with patch('pathlib.Path.is_symlink',autospec=True,side_effect=lambda path:path.suffix==suffix), \
                        patch('nailblog.images.pipeline._write_atomic') as writer:
                    with self.assertRaisesRegex(ImageError,'symlink'):write_atomic(target,{'x':1})
                    writer.assert_not_called()


class PipelineEntryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.c=settings(self.temp.name);self.out=Path(self.temp.name)/'output'

    def test_default_provider_is_the_subscription_route(self):
        self.assertEqual(self.c.image.provider,'subscription')
        self.assertIsNotNone(ImagePipeline(self.c).provider)

    async def test_api_routes_are_refused_before_anything_is_written(self):
        pipeline=ImagePipeline(self.c,provider=object())
        for flags in ({'paid_enabled':True},{'free_enabled':True}):
            with self.assertRaisesRegex(ImageError,'api_image_routes_removed'):
                await pipeline.run(article(),self.out,'run',1,**flags)
        self.assertFalse(self.out.exists())

    async def test_a_non_subscription_provider_is_refused(self):
        other=replace(self.c,image=replace(self.c.image,provider='cloudflare'))
        with self.assertRaisesRegex(ImageError,'subscription_provider_required'):
            await ImagePipeline(other,provider=object()).run(article(),self.out,'run',1,subscription_enabled=True)

    async def test_subscription_run_needs_the_explicit_enable(self):
        with self.assertRaisesRegex(ImageError,'explicit_subscription_enable_required'):
            await ImagePipeline(self.c,provider=object()).run(article(),self.out,'run',None)


if __name__=='__main__':unittest.main()
