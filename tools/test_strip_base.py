"""Gap base must not depend on detector scale; legacy grades remain intact."""
from pathlib import Path
import sys,os,tempfile,copy
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np,strip,desktop_model as model
from tiffio import write_tiff_rgb16
base=np.array([.68,.47,.36],np.float32)
# 2 x 6, very narrow gaps at analysis scale, unlike the old wide synthetic gaps.
small=np.full((2400,330,3),.008,np.float32)
for x0,x1 in ((10,140),(190,320)):
    small[25:2370,x0:x1]=base
    for n in range(6):small[40+n*388:400+n*388,x0:x1]=base*[.38,.32,.28]
for factor in (1,2,5):
    a=np.repeat(np.repeat(small,factor,axis=0),factor,axis=1)
    layout=strip.detect_layout(a,min_strip_width=50)
    assert layout.frame_count==12,(factor,layout)
    assert np.max(np.abs(np.array(layout.bases)-base))<.002,(factor,layout.bases)
    # Old detector behavior is deliberately preserved for project compatibility.
    del a
root=Path(__file__).resolve().parents[1];(root/'build/tests').mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(dir=root/'build/tests',prefix='base-') as tmp:
    source=Path(tmp)/'strip.tif';write_tiff_rgb16(source,small)
    opened=model.open_image(source,input_mode='strip')
    value=opened['params'];value['base']=[.31,.21,.14];value['exposure']=.23
    project=Path(tmp)/'old-grade.filmproj';model.save_project(project,value)
    decoded=model.engine.STATE['native_loaded']
    restored=model.open_project(project)
    assert model.engine.STATE['native_loaded'] is decoded
    assert restored['params']['base']==value['base'] and restored['params']['exposure']==.23
print('PASS clear gap source sampling at 3 scales, 12-frame layout, saved grade preserved')
