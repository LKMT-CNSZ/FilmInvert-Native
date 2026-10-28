"""Chart fitting, input quality, backwards pixels, native sampling and persistence."""
from pathlib import Path
import os
import sys
import json
import tempfile
import time
import importlib.util
import threading
os.environ['QT_QPA_PLATFORM']='offscreen'
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tools'))
import numpy as np
import core
import calibration
import native_app as ui
import desktop_model as model
from tiffread import read_tiff
from make_calibration_demo import make_demo

def raises(fn,part):
    try:fn()
    except ValueError as e:assert part in str(e),str(e)
    else:raise AssertionError('Expected rejection: '+part)

app=ui.QApplication([])
(ROOT/'build'/'tests').mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(dir=ROOT/'build'/'tests',prefix='chart-') as folder:
    folder=Path(folder);demo=make_demo(folder);reference=calibration.read_reference(folder/'calibration-demo-reference.csv')
    model.open_image(folder/'calibration-demo.tif')
    p=core.Params(mode='positive',black=0,white=1).__dict__
    profile=model.fit_calibration(p,reference,demo['points'],'Synthetic only')
    report=profile['report'];assert report['can_apply'] and report['valid']==24
    assert report['after_rmse']<.02 and report['cv_rmse']<.02 and report['before_rmse']>3
    assert np.max(np.abs(np.array(profile['matrix'])-np.linalg.inv(demo['mixing'])))<.001
    # Guard degenerate/insufficient/clipped samples and malformed reference input.
    raises(lambda:calibration.fit(reference,[[.2,.2,.2]]*24,[0]*24,demo['points']),'颜色变化不足')
    raises(lambda:calibration.fit(reference,[None]*24,[1]*24,demo['points']),'至少需要 8')
    raises(lambda:calibration.fit(reference,profile['measurements'],[1]*24,demo['points']),'至少需要 8')
    assert calibration.valid_matrix([[100]*3]*3) is None
    invalid=folder/'bad.csv';invalid.write_text('name,r,g,b\na,256,4,8\n'*8,encoding='utf-8')
    raises(lambda:calibration.read_reference(invalid),'参考无效')
    # Default/zero-strength/identity settings preserve exact pixels.
    lin=demo['image'];baseline=core.process(lin,core.Params(**p))
    corrected=core.Params(**dict(p,calibration=profile))
    assert not np.array_equal(core.process(lin,corrected),baseline)
    assert np.array_equal(core.process(lin,core.Params(**dict(p,calibration=profile,calibration_strength=0))),baseline)
    assert np.array_equal(core.process(lin,core.Params(**dict(p,calibration={'matrix':np.eye(3).tolist()}))),baseline)
    mono=core.Params(mode='bw_negative');old=core.process(lin,mono);mono.calibration=profile
    assert np.array_equal(core.process(lin,mono),old)
    # Full image sample coordinates follow the displayed horizontal flip.
    flip=dict(p,flip=True);mirrored=[[1-x,y] for x,y in demo['points']]
    flipped=model.fit_calibration(flip,reference,mirrored,'mirrored')
    assert np.allclose(flipped['matrix'],profile['matrix'],atol=1e-6)
    # Complete native path: grid selection, background fit, comparison, apply/undo.
    window=ui.MainWindow(folder/'profile');errors=[];window.show_error=errors.append
    def idle():
        deadline=time.monotonic()+40
        while time.monotonic()<deadline:
            app.processEvents();time.sleep(.01)
            if window.job is None and not window.pending_command and not window.preview_timer.isActive() and not any(j.isRunning() for j in window.jobs):break
        else:raise AssertionError('Qt worker timeout')
        assert not errors,errors
    window.open_path(str(folder/'calibration-demo.tif'));idle();window.set_parameters(p);idle()
    window.open_calibration();idle();dialog=window.chart_dialog
    dialog.load_reference(folder/'calibration-demo-reference.csv');dialog.start_grid()
    x0,y0,x1,y1=demo['box'];dialog.sample_at(x0,y0);dialog.sample_at(x1,y1)
    assert np.allclose(dialog.points,demo['points']);assert len(dialog.canvas.marker_items)==24
    dialog.request_fit();idle();assert dialog.apply_button.isEnabled()
    after=dialog.canvas.item.pixmap().toImage();dialog.compare.setChecked(False)
    assert after!=dialog.canvas.item.pixmap().toImage()
    dialog.apply_result();idle();assert window.p['calibration']['report']['can_apply']
    assert window.chart_dialog is None
    window.travel(-1);idle();assert window.p['calibration'] is None
    window.travel(1);idle();assert window.p['calibration'] is not None
    dest=folder/'校正.tif';window.export_to(str(dest));idle()
    expected=(core.process(model.engine.STATE['full'],model.params(window.p))*65535+.5).astype(np.uint16)
    assert np.array_equal(read_tiff(str(dest)),expected)
    recipe=json.loads(Path(str(dest)+'.json').read_text('utf-8'));assert recipe['params']['calibration']['matrix']==window.p['calibration']['matrix']
    project=folder/'校正.filmproj';window.save_project_to(str(project));idle();saved=read_tiff(str(dest)).copy()
    window.clear_calibration();idle();window.open_project_path(str(project));idle()
    window.export_to(str(dest));idle();assert np.array_equal(saved,read_tiff(str(dest)))
    # Legacy 2.1 project/recipe params without new fields normalize to no correction.
    legacy=dict(p);legacy.pop('calibration',None);legacy.pop('calibration_strength',None)
    assert model.params(legacy).calibration is None
    window.open_path(str(ROOT/'samples'/'pro-strip-demo.tif'));idle()
    window.apply_calibration(profile);idle();window.apply_roll();idle()
    window.go_frame(3);idle();window.batch_to(str(folder));idle()
    manifest=json.loads((Path(window.last_export)/'manifest.json').read_text('utf-8'))
    assert len(manifest)==6 and all(row['params']['calibration']['matrix']==profile['matrix'] for row in manifest)
    si,fi,arr=model.strip.extract_one(model.engine.STATE['strip_img'],model.engine.STATE['strip_layout'],0)
    expected=(core.process(arr,model.params(manifest[0]['params']))*65535+.5).astype(np.uint16)
    assert np.array_equal(read_tiff(str(Path(window.last_export)/manifest[0]['file'])),expected)
    window.close();app.processEvents()
print('PASS chart: known matrix recovery, held-out improvement, invalid sampling rejection, flip, native grid/fit/compare/apply/history, exact ICC TIFF + recipe/project recovery, default compatibility')
