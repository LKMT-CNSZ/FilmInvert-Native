"""Independent TIFF strip fixtures, exact export and acceptance-metric regressions."""
from pathlib import Path
import os,sys,tempfile,struct,zlib,json
os.environ['QT_QPA_PLATFORM']='offscreen'
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import native_app as ui
import numpy as np
from PIL import Image
import colorio,loader,core,server,runtime_metrics
from tiffio import write_tiff_rgb16,write_tiff_strips
from tiffread import read_tiff,read_tiff_linear,TiffError
app=ui.QApplication.instance() or ui.QApplication([])
# Independent TIFF producer: endian, depth, predictor, gray/RGB/RGBA and photometric.
def fixture(path,array,bits,endian,compress,pred,photo):
 h,w,c=array.shape;rows=3;blobs=[]
 for top in range(0,h,rows):
  a=array[top:top+rows].copy()
  if pred==2:a[:,1:]=a[:,1:]-a[:,:-1]
  raw=a.astype(endian+('u2' if bits==16 else 'u1')).tobytes()
  blobs.append(b''.join(bytes([len(raw[i:i+128])-1])+raw[i:i+128] for i in range(0,len(raw),128)) if compress=='packbits' else (zlib.compress(raw) if compress else raw))
 tags=[(256,4,1,w),(257,4,1,h),(258,3,1,bits),(259,3,1,32773 if compress=='packbits' else (8 if compress else 1)),(262,3,1,photo),(273,4,len(blobs),None),(277,3,1,c),(278,4,1,rows),(279,4,len(blobs),None),(284,3,1,1),(317,3,1,pred)]
 count=len(tags);offset=8+2+count*12+4;offsets=[];bytecounts=[]
 for blob in blobs:offsets.append(offset);bytecounts.append(len(blob));offset+=len(blob)
 extras=struct.pack(endian+'I'*len(offsets),*offsets)+struct.pack(endian+'I'*len(blobs),*bytecounts)
 out=bytearray((b'II' if endian=='<' else b'MM')+struct.pack(endian+'HIH',42,8,count))
 for tag,typ,n,value in tags:
  if value is None:value=offset if tag==273 else offset+4*len(blobs)
  field=struct.pack(endian+'H',value)+b'\0\0' if typ==3 else struct.pack(endian+'I',value)
  out+=struct.pack(endian+'HHI',tag,typ,n)+field
 out+=b'\0'*4+b''.join(blobs)+extras;path.write_bytes(out)
(ROOT/'build/tests').mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(dir=ROOT/'build/tests',prefix='stream-') as tmp:
 folder=Path(tmp);rng=np.random.default_rng(88);cases=0
 for bits in (8,16):
  for endian in ('<','>'):
   for channels in (1,3,4):
    for pred in (1,2):
     for compress in (False,True,'packbits'):
      photo=1 if channels==1 else 2
      a=rng.integers(0,2**bits,(8,7,channels),dtype=np.uint8 if bits==8 else np.uint16)
      path=folder/'fixture.tif';fixture(path,a,bits,endian,compress,pred,photo)
      expected=a[...,:min(3,channels)].astype(np.uint16)*(257 if bits==8 else 1)
      assert np.array_equal(read_tiff(path),expected),(bits,endian,channels,pred,compress)
      assert np.array_equal(read_tiff_linear(path),expected.astype(np.float32)/65535.)
      cases+=1
 a=np.arange(56,dtype=np.uint16).reshape(8,7,1);fixture(path,a,16,'>',True,2,0)
 assert np.array_equal(read_tiff(path),65535-a)
 # Quantization, strip tables, compression and ICC checked by separate Pillow parser.
 for compress in ('none','deflate','lzw'):
  for pred in (False,True):
   a=rng.random((263,31,3),dtype=np.float32)
   if compress=='lzw':a=a[:8,:5] # bounded pure-Python codec coverage
   path=folder/(compress+str(pred)+'.tif');icc=colorio.icc_profile('prophoto')
   write_tiff_rgb16(path,a,compress=compress,predictor=pred,icc_profile=icc)
   assert np.array_equal(read_tiff(path),(a*65535+.5).astype(np.uint16))
   with Image.open(path) as im:
    assert im.size==(a.shape[1],a.shape[0]) and im.info['icc_profile']==icc
    if len(a)>128:assert len(im.tag_v2[273])>=3 and im.tag_v2[278]<=128
 print(f'PASS {cases} independent 8/16bit endian/gray/RGB/RGBA/predictor/compression strip fixtures, white-is-zero, writer quantization/ICC and separate Pillow strip parsing')
 # Same-space and cross-space diagnostics retain original boundary and rounding semantics.
 wide=rng.random((279,41,3),dtype=np.float32);wide[0,0]=0;wide[-1,-1]=1
 for source in ('srgb','prophoto'):
  for target in colorio.SPACES:
   stats={};blocks=list(colorio.export_strips(wide,target,source,stats,23))
   assert np.array_equal(np.concatenate([v for _,v in blocks]),colorio.convert(wide,target,source))
   full=colorio.output_diagnostics(wide,target,source)
   assert stats['fraction']==full['fraction'] and stats['endpoints']==full['endpoints']
 # Actual end-to-end export equals full target conversion for both working domains.
 for space in ('srgb','prophoto'):
  p=core.Params(mode='positive',working_space=space,black=0,white=1,rotation=90,straighten=.5,local_edits=[dict(kind='clone',points=[[.5,.5]],offset=[-.1,0],radius=.04)])
  for target in colorio.SPACES:
   p.output_space=target;path=folder/'actual.tif';report=server.export_pixels(path,wide,p)
   expected=core.process(wide,p,target)
   assert np.array_equal(read_tiff(path),(np.clip(expected,0,1)*65535+.5).astype(np.uint16))
   assert report['space']==target
 print('PASS exact six working/target export paths, streamed/full diagnostics equality, local clone and geometric output')
 import local_edits
 for offset in ([.99,.99],[-.99,-.99],[.99,-.99],[-.99,.99]):
  op=dict(kind='clone',points=[[.5,.5]],radius=.001,offset=offset)
  assert np.array_equal(local_edits.apply(wide,[op]),wide)
 # Failure must not publish a final output through native adapter.
 import desktop_model as model
 source=folder/'source.tif';write_tiff_rgb16(source,wide);model.open_image(source,input_mode='single')
 original=server.export_pixels
 def fail(path,*args):Path(path).write_bytes(b'partial');raise OSError('injected I/O failure')
 server.export_pixels=fail
 try:
  try:model.export_image(folder/'never.tif',model.engine.STATE['params'].__dict__)
  except OSError:pass
  else:raise AssertionError('I/O failure suppressed')
 finally:server.export_pixels=original
 assert not (folder/'never.tif').exists() and not list(folder.glob('never.tif.*.part'))
 assert np.array_equal(loader._decode_linear16(np.arange(65536,dtype=np.uint16)),np.arange(65536,dtype=np.uint16).astype(np.float32)/65535.)
 result=runtime_metrics.report();assert result['memory']['available'] and result['memory']['process_lifetime_peak_working_set_bytes']>0
 payload=json.dumps(result);assert str(source) not in payload and source.name not in payload
 assert result['last_export']['status']=='completed'
 window=ui.MainWindow(folder/'profile')
 choose=ui.QFileDialog.getSaveFileName
 ui.QFileDialog.getSaveFileName=lambda *a,**k:(str(folder/'acceptance.json'),'JSON')
 try:window.acceptance_dialog()
 finally:ui.QFileDialog.getSaveFileName=choose
 assert json.loads((folder/'acceptance.json').read_text('utf-8'))['application']==model.VERSION
 window.loaded=False;window.close();app.processEvents()
 print('PASS failed write cleanup and native local acceptance report omits source paths/names')
print('PASS streaming regressions')
