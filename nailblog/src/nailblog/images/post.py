"""Bounded image decoding, no-upscale crop, final WebP and local Vietnamese cover template."""
import base64
import hashlib
import html
import io
from importlib.resources import files

from PIL import Image, ImageOps

from . import ImageError

VERSION = 'crop-webp-cover-v1'


def decode(data):
    try:
        if not isinstance(data,bytes) or not 0 < len(data) <= 16*1024*1024: raise ValueError()
        image = Image.open(io.BytesIO(data))
        if (image.format not in ('PNG','JPEG','WEBP') or getattr(image,'n_frames',1) != 1 or
                min(image.size) < 1 or image.width * image.height > 24_000_000):
            raise ValueError()
        image.load()
        return ImageOps.exif_transpose(image).convert('RGB')
    except Exception:
        raise ImageError('invalid_image_bytes_or_bounds') from None


def crop(data, size):
    image = decode(data)
    if min(image.width/size[0],image.height/size[1]) < 1:
        raise ImageError('source_resolution_insufficient')
    return ImageOps.fit(image,size,method=Image.Resampling.LANCZOS)


def webp(image, limit):
    for quality in (88,82,75,65,55,45,35):
        stream = io.BytesIO();image.save(stream,format='WEBP',quality=quality,method=6)
        data = stream.getvalue()
        if len(data) <= limit:
            return data
    raise ImageError('webp_size_exceeded')


def hashes(data):
    image = decode(data)
    small = image.convert('L').resize((9,8))
    dhash = sum((small.getpixel((x,y)) > small.getpixel((x+1,y))) << (y*8+x)
                for y in range(8) for x in range(8))
    return hashlib.sha256(data).hexdigest(),dhash


async def thumbnail(image, title):
    from playwright.async_api import async_playwright
    stream = io.BytesIO();image.save(stream,format='PNG')
    template = files('nailblog.images').joinpath('templates/cover.html').read_text(encoding='utf-8')
    markup = template.replace('{{BACKGROUND}}',base64.b64encode(stream.getvalue()).decode()).replace('{{TITLE}}',html.escape(title))
    async with async_playwright() as manager:
        browser = await manager.chromium.launch(headless=True)
        try:
            context = await browser.new_context(viewport={'width':1600,'height':900},service_workers='block')
            await context.route('**/*',lambda route:route.abort())
            page = await context.new_page()
            await page.set_content(markup,wait_until='load')
            await page.locator('img').evaluate('(image) => image.decode()')
            box = await page.locator('h1').bounding_box()
            if (not box or box['height'] > 630 or box['x'] < 200 or box['y'] < 135 or
                    box['x']+box['width'] > 1400 or box['y']+box['height'] > 765):
                raise ImageError('cover_title_outside_safe_area')
            return decode(await page.screenshot(type='png'))
        finally:
            await browser.close()

