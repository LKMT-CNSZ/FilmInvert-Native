"""Local synthetic rendering-cost benchmark; excludes disk decode and vsync wait."""
from pathlib import Path
import os,sys,time,json,statistics
os.environ['QT_QPA_PLATFORM']='offscreen'
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QPixmap,QPainter,QImage
from native_widgets import image_from_array
from native_import_reveal import SoftRevealItem
from native_hdr_canvas import PreparedReveal

app=QApplication([]);rng=np.random.default_rng(7)
a=rng.integers(0,256,(1400,1400,3),dtype=np.uint8);b=255-a
source=QPixmap.fromImage(image_from_array(a));target=QPixmap.fromImage(image_from_array(b))
item=SoftRevealItem(None);item.setPixmap(source)
out=QImage(1400,1400,QImage.Format.Format_RGB32)
def sdr(p):
    item.progress=p;painter=QPainter(out);painter.drawPixmap(0,0,target);item.paint(painter,None);painter.end()
start=time.perf_counter();prepared=PreparedReveal(a.astype('float32')/64,b.astype('float32')/64,(-200,0,1600,1400),1920,1080,203,1000)
preparation=(time.perf_counter()-start)*1000
def measure(fn,n=60):
    fn(.5);times=[]
    for p in np.linspace(.05,.95,n):
        start=time.perf_counter();fn(p);times.append((time.perf_counter()-start)*1000)
    return dict(median_ms=statistics.median(times),p95_ms=float(np.percentile(times,95)),frames=n)
record={'SDR_1400_square_QPainter_including_full_draw':measure(sdr),
        'HDR_1920x1080_CPU':measure(prepared.compose),
        'HDR_background_preparation_ms':preparation,'timer_interval_ms':16,
        'scope':'Synthetic local CPU cost, not physical display FPS; excludes DXGI upload/vsync and file decoding'}
path=ROOT/'build/reveal-performance.json';path.write_text(json.dumps(record,indent=2),encoding='utf-8');print(json.dumps(record,indent=2))
