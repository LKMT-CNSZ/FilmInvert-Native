"""最小 16bit RGB TIFF 写入器。

Pillow 对 16bit RGB 的写入支持很差（fromarray 不支持、save 会降成 8bit），
而在密度域里 8bit 化等于把暗部细节直接扔掉，所以自己写一个。

分条带写入 none / Deflate / LZW、PlanarConfiguration=1 的标准 TIFF，兼容 Photoshop / Lightroom / GIMP。
可选嵌入 sRGB ICC。
"""

from __future__ import annotations

import struct
import zlib

import numpy as np
from file_io import io_path

# TIFF 数据类型
BYTE, ASCII, SHORT, LONG, RATIONAL = 1, 2, 3, 4, 5

# 压缩方式
COMP_NONE, COMP_LZW, COMP_DEFLATE = 1, 5, 8


def _lzw_encode(data: bytes) -> bytes:
    """TIFF 变体 LZW 编码。

    仅作参考保留，不启用：纯 Python 逐字节实现，3.8MB 要跑两分钟，
    51MP 的图要跑几个小时。Deflate 更快、压得更狠，没有任何理由用这个。
    """
    out = bytearray()
    bitbuf = 0
    nbits = 0
    code_size = 9

    def emit(code: int, size: int) -> None:
        nonlocal bitbuf, nbits
        bitbuf = (bitbuf << size) | code
        nbits += size
        while nbits >= 8:
            nbits -= 8
            out.append((bitbuf >> nbits) & 0xFF)

    table = {bytes([i]): i for i in range(256)}
    next_code = 258
    emit(256, code_size)

    w = b""
    for b in data:
        wc = w + bytes([b])
        if wc in table:
            w = wc
            continue
        emit(table[w], code_size)
        if next_code < 4094:
            table[wc] = next_code
            next_code += 1
            if next_code == (1 << code_size) - 1 and code_size < 12:
                code_size += 1
        else:
            emit(256, code_size)
            table = {bytes([i]): i for i in range(256)}
            next_code = 258
            code_size = 9
        w = bytes([b])

    if w:
        emit(table[w], code_size)
    emit(257, code_size)
    if nbits:
        out.append((bitbuf << (8 - nbits)) & 0xFF)
    return bytes(out)


def _srgb_icc() -> bytes:
    try:
        from PIL import ImageCms

        return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    except Exception:
        return b""


def write_tiff_rgb16(path,srgb,with_icc=True,compress="deflate",predictor=True,icc_profile=None):
    """Encode row strips, retaining only bounded quantization/compression scratch."""
    a=np.asarray(srgb)
    if a.ndim==2:a=a[...,None]
    if a.ndim!=3 or a.shape[2] not in (1,3) or min(a.shape[:2])<1:raise ValueError('TIFF 需要非空 RGB / 灰度图像')
    h,w=a.shape[:2];rows=strip_rows(w)
    write_tiff_strips(path,(h,w),((y,a[y:y+rows]) for y in range(0,h,rows)),with_icc,compress,predictor,icc_profile,rows)


def strip_rows(width):
    return max(1,min(128,2*1024*1024//max(1,width*12)))


def write_tiff_strips(path,shape,strips,with_icc=True,compress='deflate',predictor=True,icc_profile=None,rows=None):
    """strips yields contiguous (first_row, encoded float RGB) with declared rows."""
    h,w=map(int,shape);rows=rows or strip_rows(w)
    if h<1 or w<1 or rows<1 or h*w*6>=0xFFFFFFFF-4*1024*1024:raise ValueError('输出超过经典 TIFF 大小范围；BigTIFF 尚未实现')
    if compress not in ('none','deflate','lzw'):raise ValueError('未知 TIFF 压缩方式')
    comp={'none':COMP_NONE,'deflate':COMP_DEFLATE,'lzw':COMP_LZW}[compress]
    pred=2 if predictor and comp!=COMP_NONE else 1
    offsets=[];counts=[];next_row=0
    with io_path(path).open('wb') as fh:
        fh.write(b'II'+struct.pack('<HI',42,0))
        for y,block in strips:
            block=np.asarray(block,dtype=np.float32)
            if block.ndim==2:block=block[...,None]
            if y!=next_row or block.ndim!=3 or block.shape[1]!=w or block.shape[2] not in (1,3) or len(block)!=min(rows,h-y):raise ValueError('TIFF 条带顺序或形状无效')
            if not np.isfinite(block).all():raise ValueError('TIFF 像素含无效数值')
            if block.shape[2]==1:block=np.repeat(block,3,axis=2)
            data=(np.clip(block,0,1)*65535.0+.5).astype('<u2')
            if pred==2:
                d=data.astype(np.int32);d[:,1:]=d[:,1:]-d[:,:-1];data=(d&65535).astype('<u2')
            raw=data.tobytes();blob=zlib.compress(raw,6) if comp==COMP_DEFLATE else (_lzw_encode(raw) if comp==COMP_LZW else raw)
            offsets.append(fh.tell());counts.append(len(blob));fh.write(blob);next_row+=len(block)
        if next_row!=h:raise ValueError('TIFF 条带不完整')
        if fh.tell()%2:fh.write(b'\0')
        ifd_off=fh.tell()
        icc=(icc_profile if icc_profile is not None else _srgb_icc()) if with_icc else b''
        def longs(values):return values[0] if len(values)==1 else struct.pack('<'+'I'*len(values),*values)
        tags=[(256,LONG,1,w),(257,LONG,1,h),(258,SHORT,3,struct.pack('<3H',16,16,16)),
              (259,SHORT,1,comp),(262,SHORT,1,2),(273,LONG,len(offsets),longs(offsets)),
              (277,SHORT,1,3),(278,LONG,1,rows),(279,LONG,len(counts),longs(counts)),
              (282,RATIONAL,1,struct.pack('<II',72,1)),(283,RATIONAL,1,struct.pack('<II',72,1)),
              (284,SHORT,1,1),(296,SHORT,1,2),(317,SHORT,1,pred),(339,SHORT,3,struct.pack('<3H',1,1,1))]
        if icc:tags.append((34675,BYTE,len(icc),icc))
        tags.sort();cursor=ifd_off+2+12*len(tags)+4;entries=[];extras=[]
        for tag,typ,count,value in tags:
            if isinstance(value,int):field=struct.pack('<H',value)+b'\0\0' if typ==SHORT else struct.pack('<I',value)
            elif len(value)<=4:field=value.ljust(4,b'\0')
            else:
                field=struct.pack('<I',cursor);extras.append(value+b'\0'*(len(value)%2));cursor+=len(extras[-1])
            entries.append(struct.pack('<HHI',tag,typ,count)+field)
        if cursor>=0xFFFFFFFF:raise ValueError('TIFF 文件过大')
        fh.write(struct.pack('<H',len(tags)));fh.write(b''.join(entries));fh.write(b'\0'*4)
        for value in extras:fh.write(value)
        fh.seek(4);fh.write(struct.pack('<I',ifd_off))
