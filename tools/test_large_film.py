"""Large single-strip I/O, preview equivalence and isolated peak-memory checks."""
from pathlib import Path
import os,sys,struct,tempfile,subprocess,json
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
import tiffread
from PIL import Image

def single_strip(path,a,bits,endian,pred=1,photo=2):
    h,w,c=a.shape
    tags=[(256,4,w),(257,4,h),(258,3,bits),(259,3,1),(262,3,photo),
          (273,4,8+2+12*11+4),(277,3,c),(278,4,h),(279,4,a.nbytes),(284,3,1),(317,3,pred)]
    header=bytearray((b'II' if endian=='<' else b'MM')+struct.pack(endian+'HIH',42,8,len(tags)))
    for tag,typ,value in tags:
        header+=struct.pack(endian+'HHI',tag,typ,1)+(struct.pack(endian+'H',value)+b'\0\0' if typ==3 else struct.pack(endian+'I',value))
    b=a.copy()
    if pred==2:b[:,1:]=b[:,1:]-b[:,:-1]
    path.write_bytes(header+b'\0'*4+b.astype(endian+('u2' if bits==16 else 'u1')).tobytes())

if len(sys.argv)>1 and sys.argv[1]=='--memory-child':
    from runtime_metrics import memory
    a=tiffread.read_tiff_linear(sys.argv[2]);assert a.shape==(4096,4096,3)
    assert np.all(a[0]==np.float32(12345)/np.float32(65535))
    print(json.dumps({'bytes':a.nbytes,'memory':memory()}));raise SystemExit(0)

(ROOT/'build/tests').mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(dir=ROOT/'build/tests',prefix='large-film-') as temporary:
    folder=Path(temporary);path=folder/'single.tif';rng=np.random.default_rng(333)
    # Force blocks to cross many scanlines and keep an odd final block. The
    # producer uses one strip, including endian/predictor/alpha/white-is-zero.
    original_budget=tiffread.READ_BLOCK_BYTES;tiffread.READ_BLOCK_BYTES=791
    try:
        for bits in (8,16):
            for endian in ('<','>'):
                for channels in (1,3,4):
                    for pred in (1,2):
                        photo=0 if channels==1 else 2
                        a=rng.integers(0,2**bits,(37,31,channels),dtype=np.uint8 if bits==8 else np.uint16)
                        single_strip(path,a,bits,endian,pred,photo)
                        expected=a[...,:min(channels,3)].astype(np.uint16)*(257 if bits==8 else 1)
                        if photo==0:expected=65535-expected
                        assert np.array_equal(tiffread.read_tiff(path),expected)
                        assert np.array_equal(tiffread.read_tiff_linear(path),expected.astype(np.float32)/65535.)
        # Instrument an independent byte source: no request for the full strip.
        class Tracking:
            def __init__(self,data):self.data=data;self.reads=[]
            def __len__(self):return len(self.data)
            def __getitem__(self,key):
                value=self.data[key];self.reads.append(len(value));return value
        source=Tracking(path.read_bytes());tiffread._read_pixels(source,True)
        assert max(source.reads)<=tiffread.READ_BLOCK_BYTES
        truncated=folder/'short.tif';truncated.write_bytes(path.read_bytes()[:-1])
        try:tiffread.read_tiff_linear(truncated)
        except tiffread.TiffError:pass
        else:raise AssertionError('truncated strip accepted')
    finally:tiffread.READ_BLOCK_BYTES=original_budget
    print('PASS 24 single-strip fixtures / 48 exact float/uint16 decodes, bounded read ranges, truncated source',flush=True)
    import server
    def prior(lin,max_side):
        h,w=lin.shape[:2];scale=max_side/max(h,w)
        if scale>=1:return lin
        nh,nw=max(1,int(h*scale)),max(1,int(w*scale));out=np.empty((nh,nw,3),np.float32)
        for c in range(3):
            ch=(np.clip(lin[...,c],0,1)*65535.).astype(np.uint16)
            out[...,c]=np.asarray(Image.fromarray(ch).resize((nw,nh),Image.LANCZOS),np.float32)/65535.
        return out
    image=rng.random((333,517,3),dtype=np.float32)*1.4-.2
    image[0,0]=[0,1,np.nextafter(np.float32(1),np.float32(0))]
    for view in (image,image[:,::-1],np.rot90(image),image[::3,::2]):
        for size in (51,200,2000):assert np.array_equal(server.downscale(view,size),prior(view,size))
    # Same preview arithmetic for 16-bit quantization boundaries, strided input,
    # Lanczos resize and no-resize identity; source pixels remain unchanged.
    assert np.array_equal(image[0,0],[0,1,np.nextafter(np.float32(1),np.float32(0))])
    print('PASS exact prior Lanczos preview for clipping endpoints, strides, rotation and no-resize',flush=True)
    # Produce a 16 MP single-strip input without allocating a full RGB fixture.
    width=4096;height=4096;row=np.full((1,width,3),12345,np.uint16)
    single_strip(path,row,16,'>')
    header=bytearray(path.read_bytes()[:146])
    # This independent 11-tag header has scalar height / RowsPerStrip / count.
    for index,value in ((1,height),(7,height),(8,height*width*6)):
        struct.pack_into('>I',header,10+12*index+8,value)
    with path.open('wb') as stream:
        stream.write(header);block=np.repeat(row.astype('>u2'),32,axis=0).tobytes()
        for _ in range(height//32):stream.write(block)
    child=subprocess.run([sys.executable,'-I','-X','utf8',str(Path(__file__).resolve()),'--memory-child',str(path)],capture_output=True,encoding='utf-8',errors='replace',timeout=60)
    assert child.returncode==0,child.stdout+child.stderr
    result=json.loads(child.stdout.splitlines()[-1])
    if result['memory']['available']:
        assert result['memory']['process_lifetime_peak_working_set_bytes']<result['bytes']+128*1024*1024,result
    print('PASS isolated 16 MP single-strip decoder peak < final float32 image + 128 MiB',flush=True)
