from PIL import Image, ImageOps
import pytest

from novus_wechat.native_image_once import prepare
from novus_wechat.native_sender import validate


@pytest.mark.parametrize('format',['PNG','JPEG','WEBP','BMP','GIF'])
def test_static_images_normalize_to_native_png(tmp_path,format,monkeypatch):
    monkeypatch.setattr('novus_wechat.native_sender.sys.platform','win32')
    source=tmp_path/'original.image'
    Image.new('RGB',(9,7),(140,20,90)).save(source,format=format)
    validate({'username':'wxid_person','files':[str(source)],'images':True})
    request=prepare(source,tmp_path/'outbox',recipient='wxid_person')
    with Image.open(source) as before,Image.open(request.parent/'image.png') as after:
        assert after.format=='PNG'
        assert after.size==before.size
        assert after.convert('RGBA').tobytes()==ImageOps.exif_transpose(before).convert('RGBA').tobytes()
    if format=='PNG':
        assert (request.parent/'image.png').read_bytes()==source.read_bytes()


def test_jpeg_orientation_preserved_and_animation_not_silently_flattened(tmp_path):
    source=tmp_path/'rotate.jpg'
    image=Image.new('RGB',(8,12))
    exif=Image.Exif();exif[274]=6
    image.save(source,exif=exif)
    request=prepare(source,tmp_path/'outbox')
    with Image.open(request.parent/'image.png') as normalized:
        assert normalized.size==(12,8)
    animation=tmp_path/'animated.gif'
    Image.new('RGB',(8,8),'red').save(animation,save_all=True,append_images=[Image.new('RGB',(8,8),'blue')])
    with pytest.raises(ValueError,match='动画'):
        prepare(animation,tmp_path/'outbox')
