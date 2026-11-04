import base64
import io
import math
import os
from pathlib import Path
import socket
import subprocess
import unittest
from PIL import Image,ImageFilter
from source_mirror.isolated_image_compare import compare

class ImageCompareTests(unittest.TestCase):
    def encode(self,image,fmt='JPEG',**options):
        stream=io.BytesIO();image.save(stream,format=fmt,**options);return stream.getvalue()
    def request(self,original,output,policy=None):
        return {'original':base64.b64encode(original).decode(),'output':base64.b64encode(output).decode(),
                'policy':policy if policy is not None else {'mode':'general','max_rms':0.1,'max_channel_delta':1,'max_aspect_error':0,'min_scale':1,'resample':'none'}}
    def test_lossless_reencode_can_pass_without_byte_identity(self):
        image=Image.new('RGB',(40,40),'white')
        original=self.encode(image,'PNG',compress_level=0);output=self.encode(image,'PNG',compress_level=9)
        self.assertNotEqual(original,output)
        result=compare(self.request(original,output));self.assertEqual(result['status'],'VISUAL_QA_NOT_PUBLISHABLE')
        self.assertEqual(result['width'],40);self.assertEqual(result['max_channel_delta'],0)
    def test_same_dhash_different_pixels_crop_and_blur_fail(self):
        white=Image.new('RGB',(40,40),'white');gray=Image.new('RGB',(40,40),'gray')
        def dhash(image):
            values=list(image.convert('L').resize((9,8)).getdata())
            return tuple(values[y*9+x]>values[y*9+x+1] for y in range(8) for x in range(8))
        self.assertEqual(dhash(white),dhash(gray))
        with self.assertRaisesRegex(ValueError,'difference'):compare(self.request(self.encode(white),self.encode(gray)))
        pattern=Image.new('RGB',(40,40),'white')
        for x in range(0,20):
            for y in range(40):pattern.putpixel((x,y),(0,0,0))
        original=self.encode(pattern,quality=100,subsampling=0)
        shifted=self.encode(pattern.crop((5,0,40,40)).resize((40,40)),quality=100,subsampling=0)
        blurred=self.encode(pattern.filter(ImageFilter.GaussianBlur(3)),quality=100,subsampling=0)
        for changed in (shifted,blurred):
            with self.assertRaisesRegex(ValueError,'difference'):compare(self.request(original,changed))
        with self.assertRaisesRegex(ValueError,'dimensions'):compare(self.request(original,self.encode(pattern.resize((39,40)))))
    def test_missing_unbounded_policy_invalid_image_and_alpha_hold(self):
        original=self.encode(Image.new('RGB',(2,2),'white'))
        for policy in ({},{'max_rms':float('nan'),'max_channel_delta':1},{'max_rms':1,'max_channel_delta':True},
                       {'mode':'general','max_rms':255,'max_channel_delta':255,'max_aspect_error':0,'min_scale':1,'resample':'none'}):
            with self.subTest(policy=policy),self.assertRaises(ValueError):compare(self.request(original,original,policy))
        with self.assertRaises(Exception):compare(self.request(original,b'invalid'))
        alpha=self.encode(Image.new('RGBA',(2,2),'white'),'PNG')
        with self.assertRaises(ValueError):compare(self.request(alpha,alpha))
    def test_downscale_uses_fixed_reference_and_rejects_crop_or_wrong_aspect(self):
        source=Image.new('RGB',(40,40),'white')
        for x in range(15):
            for y in range(40):source.putpixel((x,y),(0,0,0))
        expected=source.resize((20,20),Image.Resampling.LANCZOS)
        policy={'mode':'general','max_rms':0,'max_channel_delta':0,'max_aspect_error':0.001,'min_scale':0.4,'resample':'lanczos'}
        result=compare(self.request(self.encode(source,'PNG'),self.encode(expected,'PNG'),policy))
        self.assertEqual((result['original_width'],result['width']),(40,20))
        cropped=source.crop((5,0,40,40)).resize((20,20),Image.Resampling.LANCZOS)
        with self.assertRaisesRegex(ValueError,'difference'):compare(self.request(self.encode(source,'PNG'),self.encode(cropped,'PNG'),policy))
        with self.assertRaisesRegex(ValueError,'aspect'):compare(self.request(self.encode(source,'PNG'),self.encode(source.resize((20,19)),'PNG'),policy))
    def test_scoped_pair_threshold_cannot_be_applied_to_other_images(self):
        image=self.encode(Image.new('RGB',(2,2),'white'),'PNG')
        rule={'mode':'pexels-observed-pair','max_rms':3,'max_channel_delta':48,'max_aspect_error':0.001,'min_scale':0.33,'resample':'lanczos'}
        with self.assertRaisesRegex(ValueError,'pair not allowlisted'):compare(self.request(image,image,rule))
        rule['mode']='general'
        with self.assertRaisesRegex(ValueError,'invalid comparison policy'):compare(self.request(image,image,rule))


@unittest.skipUnless(os.name=='posix' and hasattr(socket,'SO_PEERCRED') and os.geteuid()>0,'requires non-root Linux')
class IsolatedProcessTests(unittest.TestCase):
    def run_helper(self,allowed_uid):
        image=Image.new('RGB',(2,2),'white');stream=io.BytesIO();image.save(stream,format='PNG')
        raw=stream.getvalue();encoded=base64.b64encode(raw).decode()
        request=__import__('json').dumps({'original':encoded,'output':encoded,'policy':{'mode':'general','max_rms':0,'max_channel_delta':0,'max_aspect_error':0,'min_scale':1,'resample':'none'}}).encode()
        parent,child=socket.socketpair();proc=None
        try:
            script=Path(__file__).resolve().parents[1]/'source_mirror'/'isolated_image_compare.py'
            proc=subprocess.Popen(['/usr/bin/python3','-I',str(script)],stdin=child,stdout=child,stderr=subprocess.DEVNULL,
                env={'SOURCE_MIRROR_COMPARE_ALLOWED_UID':str(allowed_uid)},close_fds=True)
            child.close();parent.settimeout(20);result=bytearray()
            try:
                parent.sendall(request);parent.shutdown(socket.SHUT_WR)
                while True:
                    part=parent.recv(4096)
                    if not part:break
                    result.extend(part)
            except (ConnectionResetError,BrokenPipeError):pass
            status=proc.wait(timeout=20)
            try:value=__import__('json').loads(result) if result else None
            except ValueError:value=None
            return status,value
        finally:
            parent.close();child.close()
            if proc is not None and proc.poll() is None:proc.kill();proc.wait()
    def test_allowed_peer(self):
        status,result=self.run_helper(os.geteuid());self.assertEqual(status,0);self.assertIs(result['ok'],True)
    def test_wrong_peer_is_rejected(self):
        status,result=self.run_helper(os.geteuid()+1);self.assertNotEqual(status,0)
        if result is not None:self.assertIs(result['ok'],False)
