"""最小 TIFF 读取器（16bit RGB 场景专用）。

Pillow 确实会把 16bit RGB TIFF 读成 8bit（mode 报 RGB，数据是 uint8），
在密度域里这等于直接砍掉暗部，所以自己读。

支持：
  压缩   none(1) / CCITT 不支持 / LZW(5) / Deflate(8,32946) / PackBits(32773)
  采样   1 通道灰度、3 通道 RGB、4 通道 RGBA（丢 alpha）
  位深   8 / 16
  布局   chunky (PlanarConfiguration=1)
  字节序 II / MM
"""

from __future__ import annotations

import struct
import zlib

import numpy as np

T_NONE, T_LZW, T_DEFLATE, T_PACKBITS = 1, 5, 8, 32773
T_DEFLATE_OLD = 32946

# A scan can have a single strip containing hundreds of MB. Bound decoded row
# scratch independently of the producer's RowsPerStrip (one row is the minimum).
READ_BLOCK_BYTES = 8 * 1024 * 1024


class TiffError(RuntimeError):
    pass


def _lzw_decode(data: bytes, expected: int | None = None) -> bytes:
    dic = {i: bytes([i]) for i in range(256)}
    out = bytearray()
    next_code, code_size, prev = 258, 9, None
    nbits = len(data) * 8
    pos = 0

    def read_code(pos: int, width: int) -> tuple[int, int]:
        code = 0
        for _ in range(width):
            if pos >= nbits:
                return -1, pos
            byte = data[pos >> 3]
            code = (code << 1) | ((byte >> (7 - (pos & 7))) & 1)
            pos += 1
        return code, pos

    while True:
        code, pos = read_code(pos, code_size)
        if code < 0:
            break
        if code == 256:
            dic = {i: bytes([i]) for i in range(256)}
            next_code, code_size, prev = 258, 9, None
            continue
        if code == 257:
            break
        if prev is None:
            if code not in dic:
                raise TiffError("LZW: bad first code")
            entry = dic[code]
        else:
            if code in dic:
                entry = dic[code]
            elif code == next_code:
                entry = prev + prev[:1]
            else:
                raise TiffError(f"LZW: bad code {code}")
            dic[next_code] = prev + entry[:1]
            next_code += 1
            if next_code == (1 << code_size) - 1 and code_size < 12:
                code_size += 1
        out += entry
        prev = entry
        if expected is not None and len(out) >= expected:
            break
    return bytes(out)


def _packbits_decode(data: bytes) -> bytes:
    out = bytearray()
    i, n = 0, len(data)
    while i < n:
        h = struct.unpack("b", data[i:i + 1])[0]
        i += 1
        if h >= 0:
            out += data[i:i + h + 1]
            i += h + 1
        elif h != -128:
            if i < n:
                out += data[i:i + 1] * (1 - h)
                i += 1
    return bytes(out)


def _read_ifd(f: bytes, off: int) -> dict:
    bo = "<" if f[:2] == b"II" else ">"
    (n,) = struct.unpack(bo + "H", f[off:off + 2])
    sizes = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}
    fmt = {1: "B", 3: "H", 4: "I", 5: "II", 8: "h", 9: "i", 11: "f", 12: "d"}
    tags: dict[int, object] = {}
    for i in range(n):
        p = off + 2 + 12 * i
        tag, typ, cnt = struct.unpack(bo + "HHI", f[p:p + 8])
        nb = sizes.get(typ, 1) * cnt
        if nb <= 4:
            payload = f[p + 8:p + 8 + nb]
        else:
            (o,) = struct.unpack(bo + "I", f[p + 8:p + 12])
            payload = f[o:o + nb]
        if typ in fmt:
            vals = struct.unpack(bo + fmt[typ] * cnt, payload[:struct.calcsize(bo + fmt[typ] * cnt)])
        else:
            vals = payload
        tags[tag] = vals[0] if cnt == 1 and typ in fmt and typ != 5 else vals
    return tags


def _read_pixels(f,linear=False):
    if f[:2] not in (b"II",b"MM"):raise TiffError('不是 TIFF 文件')
    bo='<' if f[:2]==b'II' else '>'
    if struct.unpack(bo+'H',f[2:4])[0]!=42:raise TiffError('BigTIFF 尚未实现')
    t=_read_ifd(f,struct.unpack(bo+'I',f[4:8])[0])
    w,h=int(t[256]),int(t[257]);bps=t.get(258,8);bits=int(bps[0] if isinstance(bps,tuple) else bps)
    spp=int(t.get(277,1));comp=int(t.get(259,1));photo=int(t.get(262,2));pred=int(t.get(317,1));rows=int(t.get(278,h))
    if w<1 or h<1 or rows<1 or spp not in (1,3,4) or bits not in (8,16,32):raise TiffError('不支持的 TIFF 尺寸 / 通道 / 位深')
    if isinstance(bps,tuple) and any(b!=bits for b in bps):raise TiffError('不支持混合位深')
    sample=int(t.get(339,1)[0] if isinstance(t.get(339,1),tuple) else t.get(339,1))
    floating=bits==32 and sample==3
    if int(t.get(284,1))!=1 or (not floating and (sample!=1 or bits==32)):raise TiffError('仅支持 chunky 整数或 float32 TIFF')
    if floating and (pred!=1 or photo!=2 or spp!=3):raise TiffError('float32 TIFF 需要 RGB 且无 predictor')
    if photo not in (0,1,2) or (spp>1 and photo!=2):raise TiffError('不支持的 TIFF 色彩模型')
    if pred not in (1,2):raise TiffError('不支持的 Predictor')
    offsets=t.get(273);counts=t.get(279)
    offsets=(offsets,) if isinstance(offsets,int) else offsets;counts=(counts,) if isinstance(counts,int) else counts
    if not offsets or not counts or len(offsets)!=len(counts) or len(offsets)!=(h+rows-1)//rows:raise TiffError('TIFF 条带数量无效')
    out=np.empty((h,w,1 if spp==1 else 3),np.float32 if linear or floating else np.uint16)
    for i,(offset,count) in enumerate(zip(offsets,counts)):
        top=i*rows;nrows=min(rows,h-top);expected=nrows*w*spp*(bits//8)
        if offset<0 or count<0 or offset+count>len(f):raise TiffError('TIFF 条带超出文件范围')
        if comp==T_NONE:
            if count<expected:raise TiffError('TIFF 条带数据不完整')
            raw=None
        else:
            blob=f[offset:offset+count]
            if comp in (T_DEFLATE,T_DEFLATE_OLD):raw=zlib.decompress(blob)
            elif comp==T_PACKBITS:raw=_packbits_decode(blob)
            elif comp==T_LZW:raw=_lzw_decode(blob,expected)
            else:raise TiffError('不支持的 TIFF 压缩方式')
            del blob
            if len(raw)<expected:raise TiffError('TIFF 条带数据不完整')
        row_bytes=w*spp*(bits//8);step=max(1,READ_BLOCK_BYTES//row_bytes)
        for row in range(0,nrows,step):
            nr=min(step,nrows-row);start=row*row_bytes;stop=start+nr*row_bytes
            block=f[offset+start:offset+stop] if raw is None else memoryview(raw)[start:stop]
            a=np.frombuffer(block,dtype=bo+'f4' if floating else bo+'u2' if bits==16 else 'u1').reshape(nr,w,spp)
            if bits==8:
                if pred==2:a=np.add.accumulate(a,axis=1,dtype=np.uint8)
                a=a.astype(np.uint16)*257
            elif pred==2:
                a=np.add.accumulate(a,axis=1,dtype=np.uint16)
            if photo==0:a=65535-a
            target=out[top+row:top+row+nr];a=a[...,:target.shape[2]]
            if floating:target[:]=a
            elif linear:np.divide(a,np.float32(65535),out=target)
            else:target[:]=a
            del a,block
        del raw
    return out


def _mapped_read(path,linear=False):
    # Read small ranges rather than mapping the whole scan: on Windows the
    # mapped source pages otherwise remain in the process working set alongside
    # the final float image. Keep the original handle open for the whole decode.
    import os
    from file_io import io_path
    with io_path(path).open('rb') as fh:
        size=os.fstat(fh.fileno()).st_size
        if size<8:raise TiffError('TIFF 文件头不完整')
        class Source:
            def __len__(self):return size
            def __getitem__(self,key):
                start,stop,step=key.indices(size)
                if step!=1:raise TiffError('TIFF 读取范围无效')
                fh.seek(start);return fh.read(max(0,stop-start))
        return _read_pixels(Source(),linear)


def read_tiff(path):
    """Decode strips into final uint16 RGB/gray; uncompressed reads are bounded."""
    return _mapped_read(path)


def read_tiff_linear(path):
    """Decode straight into float32 without a full-frame uint16 staging array."""
    return _mapped_read(path,True)
