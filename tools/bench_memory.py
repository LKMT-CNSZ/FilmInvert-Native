"""Isolated Windows peak-RSS comparison against a separately supplied prior app."""
from pathlib import Path
import argparse,json,subprocess,sys,time,ctypes,os
ROOT=Path(__file__).resolve().parents[1]

def peak():
 class C(ctypes.Structure):
  _fields_=[('cb',ctypes.c_ulong),('faults',ctypes.c_ulong)]+[(n,ctypes.c_size_t) for n in ('peak','working','p1','p2','p3','p4','page','page_peak','private')]
 c=C();c.cb=ctypes.sizeof(c);kernel=ctypes.windll.kernel32;kernel.GetCurrentProcess.restype=ctypes.c_void_p
 assert ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.c_void_p(kernel.GetCurrentProcess()),ctypes.byref(c),c.cb)
 return c.peak

parser=argparse.ArgumentParser();parser.add_argument('--baseline');parser.add_argument('--app',default=str(ROOT));parser.add_argument('--output');parser.add_argument('--worker',choices=['write','read','export']);parser.add_argument('--fixture');parser.add_argument('--destination')
a=parser.parse_args()
if a.worker:
 sys.path.insert(0,str(Path(a.app).resolve()));import loader,numpy as np
 from tiffio import write_tiff_rgb16
 started=time.perf_counter()
 if a.worker=='read':
  from tiffread import read_tiff_linear
  out=read_tiff_linear(a.fixture);assert out.shape==(2048,3072,3)
 else:
  array=np.random.default_rng(300).random((2048,3072,3),dtype=np.float32)
  if a.worker=='write':write_tiff_rgb16(a.destination,array)
  else:
   import server,core
   server.export_pixels(a.destination,array,core.Params(mode='positive',black=0,white=1,working_space='prophoto',output_space='srgb'))
 print(json.dumps({'peak_working_set_bytes':peak(),'seconds':time.perf_counter()-started,'shape':[2048,3072,3]}));raise SystemExit(0)
if not a.baseline or not a.output:parser.error('--baseline and --output required for comparison')
sys.path.insert(0,str(Path(a.app).resolve()));import loader,numpy as np
from tiffio import write_tiff_rgb16
from tiffread import read_tiff
folder=Path(a.output).resolve().parent;folder.mkdir(parents=True,exist_ok=True);fixture=folder/'benchmark-input.tif'
write_tiff_rgb16(fixture,np.random.default_rng(300).random((2048,3072,3),dtype=np.float32))
results={}
for mode in ('write','read','export'):
 results[mode]={}
 for label,app_path in (('baseline',a.baseline),('current',a.app)):
  destination=folder/f'{mode}-{label}.tif'
  command=[sys.executable,'-I',str(Path(__file__).resolve()),'--worker',mode,'--app',str(Path(app_path).resolve()),'--fixture',str(fixture),'--destination',str(destination)]
  child=subprocess.run(command,capture_output=True,encoding='utf-8',errors='replace',timeout=180)
  if child.returncode:raise RuntimeError(child.stdout+child.stderr)
  results[mode][label]=json.loads(child.stdout.splitlines()[-1])
 baseline=results[mode]['baseline']['peak_working_set_bytes'];current=results[mode]['current']['peak_working_set_bytes']
 results[mode]['peak_reduction_fraction']=1-current/baseline
 if mode!='read':assert np.array_equal(read_tiff(folder/f'{mode}-baseline.tif'),read_tiff(folder/f'{mode}-current.tif'))
 print(mode,json.dumps(results[mode]),flush=True)
report={'format':'film-invert-memory-comparison','version':1,'runtime':sys.version,'baseline':str(Path(a.baseline).resolve()),'current':str(Path(a.app).resolve()),'pixels':2048*3072,'checks':results,'scope':'Each child is independent. Peak working set includes startup and image allocation; not a hard memory cap or a real RAW performance claim.'}
Path(a.output).write_text(json.dumps(report,indent=2),'utf-8')
print('PASS isolated peak-memory measurements; write/export TIFF pixels exactly match baseline')
