"""Disposable, byte-bounded caches. No original or edit state is cached here."""
from collections import OrderedDict
import hashlib
import json
import threading
from pathlib import Path
import numpy as np
from PIL import Image
from file_io import io_path,temporary_path


class PreviewCache:
    def __init__(self,budget=128*1024*1024):
        self.budget=budget;self.entries=OrderedDict();self.bytes=0
    def get(self,key):
        value=self.entries.pop(key,None)
        if value is not None:self.entries[key]=value
        return value
    def put(self,key,pixels,hist,diagnostics=None):
        size=pixels.nbytes+sum(h.nbytes for h in hist)
        if diagnostics is not None:size+=diagnostics["low"].nbytes+diagnostics["high"].nbytes
        old=self.entries.pop(key,None)
        if old:self.bytes-=old[2]
        if size>self.budget:return
        while self.entries and self.bytes+size>self.budget:
            _,value=self.entries.popitem(last=False);self.bytes-=value[2]
        pixels=pixels.copy();pixels.flags.writeable=False
        hist=[h.copy() for h in hist]
        self.entries[key]=(pixels,hist,size,diagnostics);self.bytes+=size


class ThumbnailCache:
    def __init__(self,folder,budget=256*1024*1024):
        self.folder=io_path(folder);self.folder.mkdir(parents=True,exist_ok=True)
        self.budget=budget;self.lock=threading.Lock()
    def key(self,asset):
        path=Path(asset['path']);stat=io_path(path).stat()
        # Edits must not reuse the thumbnail of an earlier snapshot.
        value=[str(path),stat.st_size,stat.st_mtime_ns,asset.get('snapshot')]
        return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    def path(self,key):return self.folder/(key+'.jpg')
    def save(self,key,image):
        image=image.copy();image.thumbnail((240,160),Image.Resampling.LANCZOS)
        import io,os,uuid
        stream=io.BytesIO();image.convert('RGB').save(stream,'JPEG',quality=85)
        with self.lock:
            target=self.path(key);temp=temporary_path(target)
            try:temp.write_bytes(stream.getvalue());os.replace(temp,target)
            finally:temp.unlink(missing_ok=True)
            files=sorted(self.folder.glob('*.jpg'),key=lambda p:p.stat().st_mtime_ns)
            total=sum(p.stat().st_size for p in files)
            for file in files:
                if total<=self.budget:break
                total-=file.stat().st_size;file.unlink(missing_ok=True)
        return target
    def original(self,asset):
        from loader import RAW_EXTS,rawpy
        key=self.key(asset);target=self.path(key)
        if target.is_file():return str(target),'已修片' if asset.get('snapshot') else '原片'
        fallback=self.path('original-'+key)
        if fallback.is_file():return str(fallback),'原片（尚无修片预览）'
        # Camera embedded thumbnails avoid decoding a full RAW in the library.
        path=Path(asset['path'])
        if path.suffix.lower() in RAW_EXTS:
            import io
            with rawpy.imread(str(path)) as raw:thumb=raw.extract_thumb()
            if isinstance(thumb.data,bytes):
                with Image.open(io.BytesIO(thumb.data)) as source:image=source.copy()
            else:image=Image.fromarray(thumb.data)
        else:
            from PIL import ImageOps
            with Image.open(path) as source:
                if source.width*source.height*16>256*1024*1024:
                    raise ValueError('原片较大，请先打开修片以生成预览')
                source.draft('RGB',(240,160));source.thumbnail((240,160))
                image=ImageOps.exif_transpose(source).convert('RGB')
        # Original thumbnails use a separate key: a later rendered positive replaces it.
        return str(self.save('original-'+key,image)),'原片（尚无修片预览）'
