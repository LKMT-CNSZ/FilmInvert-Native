"""Independent target-matrix, actual TIFF and display-only editor regressions."""
from pathlib import Path
import os,sys,tempfile,copy,json
os.environ['QT_QPA_PLATFORM']='offscreen'
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import native_app as ui
import numpy as np
import colorio,core,desktop_model as model,geometry
from input_color import display_working
from tiffio import write_tiff_rgb16
from tiffread import read_tiff
from native_geometry import GeometryDialog
from native_frames import FrameLayoutDialog
from native_calibration import CalibrationDialog
app=ui.QApplication.instance() or ui.QApplication([])
# Independent published-primary XYZ construction and Bradford adaptation, float64.
def matrix(xy,white):
    v=np.array([[x/y,1,(1-x-y)/y] for x,y in xy]).T
    return v@np.diag(np.linalg.solve(v,white))
mats={'srgb':matrix([(.64,.33),(.30,.60),(.15,.06)],[.95047,1,1.08883]),
      'adobe_rgb':matrix([(.64,.33),(.21,.71),(.15,.06)],[.95047,1,1.08883]),
      'prophoto':matrix([(.7347,.2653),(.1596,.8404),(.0366,.0001)],[.9642,1,.8249])}
b=np.array([[.8951,.2664,-.1614],[-.7502,1.7135,.0367],[.0389,-.0685,1.0296]])
adapt=np.linalg.solve(b,np.diag((b@np.array([.95047,1,1.08883]))/(b@np.array([.9642,1,.8249])))@b)
codes=np.array([[[.1,.65,.15],[.65,.1,.25],[.5,.5,.5],[0,0,0],[1,1,1]]],np.float32)
for target in colorio.SPACES:
    lin=np.where(codes.astype(float)<.03125,codes.astype(float)/16,codes.astype(float)**1.8)
    expected=lin if target=='prophoto' else lin@(np.linalg.solve(mats[target],adapt@mats['prophoto'])).T
    actual=colorio.target_linear(codes,target,'prophoto')
    assert np.max(np.abs(expected-actual))<2e-7
    d=colorio.output_diagnostics(codes,target,'prophoto')
    assert np.array_equal(d['low'],np.any(expected < -1e-6,axis=2))
    assert np.array_equal(d['high'],np.any(expected > 1+1e-6,axis=2))
assert colorio.output_diagnostics(codes,'prophoto','prophoto')['fraction']==0
assert colorio.output_diagnostics(codes,'srgb','prophoto')['fraction']>0
print('PASS independent float64 XYZ/Bradford cases distinguish sRGB and ProPhoto; neutral/endpoints are not gamut loss')
(ROOT/'build'/'tests').mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(dir=ROOT/'build'/'tests') as tmp:
    folder=Path(tmp);source=folder/'source.tif'
    data=np.tile(codes,(30,12,1));write_tiff_rgb16(source,data,icc_profile=colorio.icc_profile('prophoto'))
    opened=model.open_image(str(source),input_mode='single',input_color='prophoto',working_space='prophoto')
    p=opened['params'];p.update(mode='positive',black=0,white=1,rotation=90,crop=[.1,.1,.9,.9])
    for target in colorio.SPACES:
        p['output_space']=target
        wide=core.process(model.engine.STATE['full'],model.params(p),'prophoto')
        expected=colorio.output_diagnostics(wide,target,'prophoto')
        dest=folder/(target+'.tif');model.export_image(dest,p)
        saved=json.loads(Path(str(dest)+'.json').read_text('utf-8'))['output_diagnostics']
        assert saved['out_of_gamut_fraction']==expected['fraction']
        actual=read_tiff(str(dest));wanted=(np.clip(colorio.convert(wide,target,'prophoto'),0,1)*65535+.5).astype(np.uint16)
        assert np.array_equal(actual,wanted)
        preview=model.preview(p);assert preview['diagnostics']['fraction']==expected['fraction']
        assert all(np.array_equal(a,b) for a,b in zip(preview['hist'],expected['hist']))
    print('PASS actual full-size TIFF and recipe diagnostics, target histogram and geometry agree for all three spaces')
    monitor=colorio.icc_profile('adobe_rgb')
    gp=model.geometry_preview(p);gp['monitor']=monitor
    before=copy.deepcopy(p);g=GeometryDialog(gp,p)
    assert np.array_equal(g.canvas.pixels,(geometry.apply(gp['pixels'].astype(np.float32)/255,dict(g.value,crop=geometry.FULL))*255+.5).astype(np.uint8))
    assert p==before
    from native_widgets import image_from_array
    expected_display=display_working(geometry.apply(gp['working_pixels'],dict(g.value,crop=geometry.FULL)),'prophoto',monitor)
    assert g.canvas.item.pixmap().toImage().convertToFormat(image_from_array(expected_display).format())==image_from_array(expected_display)
    fp=model.layout_editor();fp['monitor']=monitor;f=FrameLayoutDialog(fp)
    assert np.array_equal(f.canvas.pixels,fp['pixels'])
    assert f.canvas.item.pixmap().toImage().convertToFormat(image_from_array(fp['pixels']).format())==image_from_array(display_working(fp['working_pixels'],'prophoto',monitor))
    cp=model.calibration_preview(p);cp['monitor']=monitor;c=CalibrationDialog(cp,None,ROOT/'samples')
    linear=c.linear.copy();points=copy.deepcopy(c.points)
    c.preview['monitor']=None;c.show_preview();default=c.canvas.item.pixmap().toImage()
    c.preview['monitor']=monitor;c.show_preview();managed=c.canvas.item.pixmap().toImage()
    assert default!=managed and np.array_equal(c.linear,linear) and c.points==points
    window=ui.MainWindow(folder/'profile')
    window.p=copy.deepcopy(p);window.loaded=True
    revision=window.revision
    window.edit_parameter('output_space','srgb')
    assert window.revision>revision  # Works even when output simulation is off.
    window.loaded=False;window.preview_timer.stop();window.history_timer.stop();window.close()
    for d in (g,f,c):d.close()
    print('PASS crop/frame/chart monitor drawing preserves compute arrays, parameters and sample points')
print('PASS output color regressions')

