"""Interoperable uncompressed RGB float32 TIFF with explicit linear-space metadata."""
from pathlib import Path
import json,struct
import numpy as np
from file_io import io_path,temporary_path


def tags(path):
    with io_path(path).open('rb') as f:
        header=f.read(8)
        if len(header)!=8 or header[:2] not in (b'II',b'MM'):return None
        order='<' if header[:2]==b'II' else '>'
        magic,offset=struct.unpack(order+'HI',header[2:])
        if magic!=42:return None
        f.seek(offset);count=struct.unpack(order+'H',f.read(2))[0]
        if count>4096:raise ValueError('TIFF IFD exceeds the metadata budget')
        entries=f.read(count*12);result={}
        sizes={1:1,2:1,3:2,4:4,7:1}
        for i in range(count):
            tag,kind,n=struct.unpack_from(order+'HHI',entries,i*12)
            if tag not in (256,257,258,259,262,270,273,277,278,279,284,339):continue
            if kind not in sizes or n*sizes[kind]>65536:continue
            payload=entries[i*12+8:i*12+12];length=n*sizes[kind]
            if length>4:
                f.seek(struct.unpack(order+'I',payload)[0]);payload=f.read(length)
            else:payload=payload[:length]
            if kind==2:value=payload.rstrip(b'\0').decode('utf-8','replace')
            elif kind in (3,4):
                values=struct.unpack(order+('H' if kind==3 else 'I')*n,payload)
                value=values[0] if n==1 else values
            else:value=payload
            result[tag]=value
        result['order']=order
        return result


def metadata(path):
    t=tags(path)
    if t is None:return None
    bits=t.get(258,8);bits=bits[0] if isinstance(bits,tuple) else bits
    if bits!=32:return None
    try:meta=json.loads(t.get(270,''))
    except (ValueError,TypeError):raise ValueError('Float TIFF needs explicit linear color-space metadata')
    if meta.get('format')!='filminvert-linear-hdr' or meta.get('space') not in ('srgb','prophoto'):raise ValueError('Unrecognized float TIFF working space')
    return meta


def write(path,array,space='srgb',kind='positive',extra=None,cancel=None):
    a=np.asarray(array)
    if a.ndim!=3 or a.shape[2]!=3 or not np.isfinite(a).all() or np.min(a)<0:raise ValueError('HDR TIFF needs finite nonnegative linear RGB')
    if space not in ('srgb','prophoto') or kind not in ('positive','negative'):raise ValueError('Invalid HDR color space or image kind')
    h,w=a.shape[:2]
    if min(h,w)<1 or h*w*12>3_900_000_000:raise ValueError('HDR TIFF exceeds the classic TIFF limit')
    meta=dict(extra or {},format='filminvert-linear-hdr',space=space,kind=kind,encoding='linear',reference_white_nits=203)
    description=json.dumps(meta,ensure_ascii=False,allow_nan=False).encode('utf-8')+b'\0'
    from PySide6.QtGui import QColorSpace
    profile=QColorSpace(QColorSpace.NamedColorSpace.SRgbLinear if space=='srgb' else QColorSpace.NamedColorSpace.ProPhotoRgb)
    profile.setTransferFunction(QColorSpace.TransferFunction.Linear)
    icc=bytes(profile.iccProfile())
    entries=[(256,4,1,w),(257,4,1,h),(258,3,3,struct.pack('<HHH',32,32,32)),
        (259,3,1,1),(262,3,1,2),(270,2,len(description),description),(273,4,1,0),
        (277,3,1,3),(278,4,1,h),(279,4,1,w*h*12),(284,3,1,1),(339,3,3,struct.pack('<HHH',3,3,3)),(34675,7,len(icc),icc)]
    offset=8+2+12*len(entries)+4;tail=bytearray();encoded=[]
    for tag,typ,count,value in entries:
        if isinstance(value,bytes):
            encoded.append((tag,typ,count,offset+len(tail)));tail.extend(value)
            if len(tail)%2:tail.append(0)
        else:encoded.append((tag,typ,count,value))
    pixels=offset+len(tail);dest=io_path(path);dest.parent.mkdir(parents=True,exist_ok=True)
    if dest.exists():raise FileExistsError('Output already exists: '+str(path))
    temp=temporary_path(dest)
    try:
        with temp.open('xb') as f:
            f.write(b'II'+struct.pack('<HIH',42,8,len(encoded)))
            for tag,typ,count,value in encoded:
                if tag==273:value=pixels
                f.write(struct.pack('<HHI',tag,typ,count))
                f.write(struct.pack('<H',value)+b'\0\0' if typ==3 and count==1 else struct.pack('<I',value))
            f.write(b'\0'*4);f.write(tail)
            for y in range(0,h,128):
                if cancel is not None and cancel.is_set():raise InterruptedError('HDR export canceled')
                f.write(np.asarray(a[y:y+128],dtype='<f4').tobytes())
            f.flush()
        import os
        if os.name=='nt':os.rename(temp,dest)  # Windows rename atomically refuses an existing target.
        else:os.link(temp,dest)
    finally:temp.unlink(missing_ok=True)
    return meta
