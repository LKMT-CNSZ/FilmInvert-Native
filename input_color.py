"""ICC-aware input: float matrix/TRC decoding and Qt display transforms.

Qt QColorSpace supports RGB/Gray ICC; unsupported profiles are rejected explicitly.
https://doc.qt.io/qt-6/qcolorspace.html
https://doc.qt.io/qt-6/qimage.html
https://www.color.org/ICC1v42_2006-05.pdf
"""
import hashlib
import numpy as np
from PIL import Image
from PySide6.QtGui import QColorSpace,QImage

CHOICES={'auto':'自动：优先嵌入 ICC','legacy':'兼容旧版：16bit 线性 / 8bit sRGB',
         'linear_srgb':'线性 sRGB','srgb':'sRGB 编码','adobe_rgb':'Adobe RGB (1998) 编码','prophoto':'ProPhoto RGB 编码'}
NAMED={'linear_srgb':QColorSpace.NamedColorSpace.SRgbLinear,'srgb':QColorSpace.NamedColorSpace.SRgb,
       'adobe_rgb':QColorSpace.NamedColorSpace.AdobeRgb,'prophoto':QColorSpace.NamedColorSpace.ProPhotoRgb}

def icc_linear(codes,data,working_space='srgb'):
    """Decode matrix/TRC ICC in floating point; avoid display-engine LUT quantization."""
    import struct,colorio
    if len(data)<132 or data[36:40]!=b'acsp' or data[16:24]!=b'RGB XYZ ' or data[8] not in (2,4):raise ValueError('输入 ICC 需要 v2 / v4 RGB matrix/TRC 配置')
    size=struct.unpack_from('>I',data)[0];count=struct.unpack_from('>I',data,128)[0]
    if size>len(data) or count>4096 or 132+12*count>size:raise ValueError('ICC 标签表损坏')
    tags={}
    for i in range(count):
        name,offset,length=struct.unpack_from('>4sII',data,132+12*i)
        if offset+length>size or length<8:raise ValueError('ICC 标签范围无效')
        tags[name]=data[offset:offset+length]
    if any(name in tags for name in (b'A2B0',b'A2B1',b'A2B2',b'D2B0',b'D2B1',b'D2B2')):
        raise ValueError('输入暂不支持 LUT ICC，请先转换为 RGB matrix/TRC 工作空间')
    def fixed(blob,offset,count):
        if offset+count*4>len(blob):raise ValueError('ICC 参数不完整')
        return np.array(struct.unpack_from('>'+str(count)+'i',blob,offset),np.float64)/65536.
    def curve(x,blob):
        if blob[:4]==b'curv':
            n=struct.unpack_from('>I',blob,8)[0]
            if n==0:return x
            if 12+2*n>len(blob):raise ValueError('ICC 曲线不完整')
            values=np.frombuffer(blob,dtype='>u2',count=n,offset=12).astype(np.float64)
            if n==1:
                if not 0<values[0]/256.<=10:raise ValueError('ICC 曲线 gamma 无效')
                return x**(values[0]/256.)
            if np.any(np.diff(values)<0):raise ValueError('ICC 曲线不是单调递增')
            return np.interp(x,np.linspace(0,1,n),values/65535.)
        if blob[:4]!=b'para':raise ValueError('不支持的 ICC 曲线类型')
        kind=struct.unpack_from('>H',blob,8)[0];counts={0:1,1:3,2:4,3:5,4:7}
        if kind not in counts:raise ValueError('不支持的 ICC 参数曲线')
        p=fixed(blob,12,counts[kind]);g=p[0]
        if not 0<g<=10:raise ValueError('ICC 曲线 gamma 无效')
        if kind==0:return x**g
        a,b=p[1:3]
        if a<=0:raise ValueError('ICC 曲线斜率无效')
        upper=np.maximum(a*x+b,0)**g
        if kind in (1,2):
            c=p[3] if kind==2 else 0
            return np.where(x>=-b/a,upper+c,c)
        c,d=p[3:5];e,f=p[5:7] if kind==4 else (0,0)
        return np.where(x>=d,upper+e,c*x+f)
    try:
        columns=[];linear=np.empty_like(codes)
        for channel,name in enumerate((b'r',b'g',b'b')):
            xyz=tags[name+b'XYZ']
            if xyz[:4]!=b'XYZ ':raise ValueError('ICC 色度标签无效')
            columns.append(fixed(xyz,8,3));linear[...,channel]=curve(codes[...,channel],tags[name+b'TRC'])
        matrix=np.stack(columns,axis=1)
        if abs(np.linalg.det(matrix))<1e-8:raise ValueError('ICC 色度矩阵不可逆')
        conversion=(np.linalg.inv(colorio.MATRICES['prophoto'])@matrix if working_space=='prophoto' else np.linalg.inv(colorio.MATRICES['srgb'])@np.linalg.inv(colorio.ADAPT)@matrix)
        result=linear@conversion.astype(np.float32).T
        if not np.isfinite(result).all():raise ValueError('ICC 产生无效数值')
        return result
    except (KeyError,struct.error) as error:
        raise ValueError('输入暂支持 RGB matrix/TRC ICC；LUT / 扫描仪查找表配置请先转换为 RGB 工作空间') from error

def named_linear(codes,choice,working_space='srgb'):
    import colorio
    if working_space=='prophoto':
        from working_color import linear
        return linear(codes if choice=='linear_srgb' else colorio.decode(codes,choice), 'srgb' if choice=='linear_srgb' else choice,'prophoto')
    if choice=='linear_srgb':return codes.copy()
    linear=colorio.decode(codes,choice)
    if choice=='srgb':return linear
    adaptation=np.linalg.inv(colorio.ADAPT) if choice=='prophoto' else np.eye(3)
    conversion=np.linalg.inv(colorio.MATRICES['srgb'])@adaptation@colorio.MATRICES[choice]
    return linear@conversion.astype(np.float32).T

def profile_space(data,target=False):
    if not isinstance(data,bytes) or len(data)>4*1024*1024:raise ValueError('ICC 超过 4 MB 或格式无效')
    space=QColorSpace.fromIccProfile(data)
    if not space.isValid() or space.colorModel()!=QColorSpace.ColorModel.Rgb or (target and not space.isValidTarget()):
        raise ValueError('该 ICC 不是受支持的 RGB 配置；请转换为 RGB 或明确选择输入编码')
    return space

def transform(rgb,source,target):
    rgb=np.asarray(rgb,np.float32)
    if rgb.ndim!=3 or rgb.shape[2]!=3:raise ValueError('色彩转换需要 RGB 图像')
    result=np.empty_like(rgb)
    # Bound transform scratch space rather than allocating full-frame RGBA copies.
    step=max(1,2*1024*1024//max(1,rgb.shape[1]*16))
    for y in range(0,rgb.shape[0],step):
        block=rgb[y:y+step];rgba=np.ones((*block.shape[:2],4),np.float32);rgba[...,:3]=block
        image=QImage(rgba.data,block.shape[1],block.shape[0],rgba.strides[0],QImage.Format.Format_RGBA32FPx4)
        image.setColorSpace(source);converted=image.convertedToColorSpace(target)
        if converted.isNull():raise ValueError('ICC 色彩转换失败')
        values=np.frombuffer(converted.constBits(),np.float32).reshape(block.shape[0],converted.bytesPerLine()//4)
        result[y:y+len(block)]=values[:,:block.shape[1]*4].reshape(*block.shape[:2],4)[...,:3]
    if not np.isfinite(result).all():raise ValueError('ICC 转换产生无效数值')
    return result

def managed_load(path,choice,working_space='srgb'):
    import loader
    from file_io import io_path
    from pathlib import Path
    if choice not in CHOICES:raise ValueError('输入色彩选项无效')
    def promote(loaded):
        if working_space=='prophoto':
            from working_color import linear
            loaded.linear=np.clip(linear(loaded.linear),0,1);loaded.working_space='prophoto'
            loaded.note+=' → 线性 ProPhoto'
            if loaded.color_info:loaded.color_info['working_space']='prophoto'
        return loaded
    if Path(path).suffix.lower() in loader.RAW_EXTS:
        if choice not in ('legacy','auto','linear_srgb'):raise ValueError('RAW 请使用自动、兼容或线性输入')
        if working_space=='prophoto':
            try:
                lin=loader._load_raw(path,'prophoto');h,w=lin.shape[:2]
                return loader.Loaded(lin,path,w,h,True,'RAW 解码器：线性 ProPhoto',choice,
                    {'selection':choice,'encoding':'RAW → 线性 ProPhoto','working_space':'prophoto','clipped_fraction':0.,'icc_sha256':None},'prophoto')
            except RuntimeError:
                if Path(path).suffix.lower() not in ('.fff','.3fr'):raise
                return promote(loader.load(path))
        loaded=loader.load(path);loaded.color=choice
        loaded.color_info={'selection':choice,'encoding':'RAW 解码器：线性 sRGB','icc_sha256':None,'clipped_fraction':0.}
        return loaded
    if choice=='legacy':return promote(loader.load(path))
    with Image.open(io_path(path)) as image:
        profile=image.info.get('icc_profile');mode=image.mode
        bits=image.tag_v2.get(258,(8,)) if hasattr(image,'tag_v2') else (16 if mode.startswith('I;16') else 8,)
        bits=bits[0] if isinstance(bits,(tuple,list)) else bits
        if choice=='auto' and profile:
            space=profile_space(profile);encoding=space.description() or '嵌入 RGB ICC'
        elif choice=='auto':
            # Keep legacy untagged decoding byte-for-byte, including old scan defaults.
            loaded=loader.load(path)
            loaded.color_info={'selection':'auto','encoding':loaded.note+'（无嵌入 ICC，默认假定）','icc_sha256':None,'clipped_fraction':0.}
            return promote(loaded)
        else:space=QColorSpace(NAMED[choice]);encoding=CHOICES[choice]
        if mode=='CMYK':raise ValueError('暂不支持 CMYK 输入，请先转换为带 RGB ICC 的图像')
        if int(bits)==16 and Path(path).suffix.lower() in ('.tif','.tiff'):
            from tiffread import read_tiff_linear
            codes=loader._to_3ch(read_tiff_linear(path))
        elif int(bits)>8:
            raise ValueError('此高位深格式暂不支持色彩管理，请导出 16bit RGB TIFF')
        else:codes=np.asarray(image.convert('RGB'),np.float32)/255.
    linear=icc_linear(codes,profile,working_space) if choice=='auto' and profile else named_linear(codes,choice,working_space)
    clipped=float(np.mean(np.any((linear<-.0001)|(linear>1.0001),axis=2)))
    linear=np.clip(linear,0,1);h,w=linear.shape[:2]
    info={'selection':choice,'encoding':encoding,'icc_sha256':hashlib.sha256(profile).hexdigest() if profile and choice=='auto' else None,
          'clipped_fraction':clipped,'working_space':working_space}
    return loader.Loaded(linear,path,w,h,False,encoding+' → 线性 '+working_space,choice,info,working_space)

def display_pixels(pixels,monitor=None,proof=None):
    source=QColorSpace(QColorSpace.NamedColorSpace.SRgb);value=np.asarray(pixels,np.float32)/255.
    if proof:
        import colorio
        encoded=colorio.convert(value,proof)
        encoded=np.rint(np.clip(encoded,0,1)*65535).astype(np.float32)/65535.
        value=colorio.convert(encoded,'srgb',proof)
    if monitor:value=transform(value,source,profile_space(monitor,True))
    return (np.clip(value,0,1)*255+.5).astype(np.uint8)

def display_working(rgb,space='prophoto',monitor=None,proof=None):
    import colorio
    value=rgb
    if proof:
        value=colorio.convert(value,proof,space)
        value=np.rint(np.clip(value,0,1)*65535).astype(np.float32)/65535.;space=proof
    if monitor:
        value=transform(value,profile_space(colorio.icc_profile(space)),profile_space(monitor,True))
    else:value=colorio.convert(value,'srgb',space)
    return (np.clip(value,0,1)*255+.5).astype(np.uint8)
