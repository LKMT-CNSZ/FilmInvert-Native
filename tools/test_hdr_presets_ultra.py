"""Saved HDR presets, real ISO gain-map JPEG/SDR base and toolbar export."""
from pathlib import Path
import os,sys,tempfile,copy,time,json,threading,io
os.environ['QT_QPA_PLATFORM']='offscreen'
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'vendor')]
import numpy as np,core,hdr_color,hdr_ultrahdr,hdr_io,desktop_model as model
from PIL import Image
from unittest.mock import patch
import native_app as ui

original=core.Params(mode='positive',black=0,white=1,exposure=.2,temp=.15).__dict__
for name in hdr_color.PRESETS:
    result=hdr_color.preset(original,name)
    assert set(k for k in result if result[k]!=original[k])<=set(hdr_color.PRESETS[name])
    assert result['exposure']==.2 and result['temp']==.15 and not original['hdr_enabled']
    p=model.params(result)
    shadow=np.full((3,4,3),.2,np.float32)
    assert np.array_equal(hdr_color.grade(shadow,p),shadow)
    ramp=np.repeat(np.linspace(.01,2,100,dtype=np.float32)[None,:,None],3,axis=2)
    graded=hdr_color.grade(ramp,p)
    assert np.all(np.diff(graded[0,:,0])>0) and graded.max()>1

api=hdr_ultrahdr.codec();assert api.ultrahdr_version()=='libultrahdr 2.0.2'
folder=ROOT/'build/hdr-ultra-tests';folder.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(dir=folder) as tmp:
    root=Path(tmp)
    # Odd dimensions, colored patches and above-white samples; inspect real
    # codec metadata and reconstruct actual pixels, not filename/signature alone.
    image=np.empty((65,97,3),np.float32);image[:,:32]=[.2,.3,.4];image[:,32:64]=[1.2,.9,.6];image[:,64:]=[3.,2.,1.]
    p=model.params(hdr_color.preset(core.Params().__dict__,'natural'))
    for space in ('srgb','prophoto'):
        p.working_space=space
        path=root/(space+' 中文.jpg');hdr,base=hdr_ultrahdr.inputs(image,p)
        hdr_ultrahdr.write(path,image,p);data=path.read_bytes()
        assert api.ultrahdr_check(data) and b'urn:iso:std:iso:ts:21496:-1' in data
        base_jpeg,gainmap,meta=api.ultrahdr_decode_jpeg(data)
        assert gainmap.startswith(b'\xff\xd8') and len(gainmap)>100
        peak=max(1.001,float(hdr[...,:3].max()))
        assert abs(meta['hdr_capacity_max']-peak)<.001 and meta['hdr_capacity_min']==1
        plain=Image.open(io.BytesIO(data));assert plain.size==(97,65) and plain.info.get('icc_profile')
        sdr=np.asarray(plain);assert np.array_equal(sdr,np.asarray(Image.open(io.BytesIO(base_jpeg))))
        decoded=api.ultrahdr_decode(data,boost=peak).astype(np.float32)[...,:3]
        sdr_linear=api.ultrahdr_decode(data,boost=1).astype(np.float32)[...,:3]
        for col in (12,46,80):
            assert np.max(np.abs(sdr[32,col].astype(float)-base[32,col,:3]))<=4
            assert np.allclose(decoded[32,col],hdr[32,col,:3],rtol=.035,atol=.025),(space,col,decoded[32,col],hdr[32,col])
            assert np.allclose(sdr_linear[32,col],hdr_color.decode(sdr[32,col]/255.),rtol=.01,atol=.005)
        assert decoded.max()>2 and sdr.max()<=255
        try:hdr_ultrahdr.write(path,image,p)
        except FileExistsError:pass
        else:raise AssertionError('Existing output overwritten')
    cancel=threading.Event();cancel.set();dest=root/'cancel.jpg'
    try:hdr_ultrahdr.write(dest,image,p,cancel)
    except InterruptedError:pass
    else:raise AssertionError('Cancellation ignored')
    cancel.clear();real=api.ultrahdr_encode
    def delayed(*a,**k):
        result=real(*a,**k);cancel.set();return result
    with patch.object(api,'ultrahdr_encode',side_effect=delayed):
        try:hdr_ultrahdr.write(dest,image,p,cancel)
        except InterruptedError:pass
        else:raise AssertionError('Cancellation after native encoder ignored')
    assert not dest.exists() and not list(root.glob('.fi-*.part'))
    for invalid in (np.ones((1,2,3)),np.full((2,2,3),np.nan),np.full((2,2,3),55)):
        try:hdr_ultrahdr.write(dest,invalid,p)
        except ValueError:pass
        else:raise AssertionError('Invalid/unrepresentable HDR accepted')

    app=ui.QApplication.instance() or ui.QApplication([])
    from PySide6.QtGui import QFontDatabase
    for font in ('msyh.ttc','segoeui.ttf'):
        path=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'/font
        if path.exists():QFontDatabase.addApplicationFont(str(path))
    source=root/'source.tif';hdr_io.write(source,image)
    window=ui.MainWindow(root/'profile');window.show();errors=[];window.show_error=errors.append
    def idle():
        end=time.monotonic()+40
        while time.monotonic()<end:
            app.processEvents();time.sleep(.005)
            if window.job is None and not window.preview_timer.isActive() and not window.preview_pending and not any(j.isRunning() for j in window.jobs):break
        else:raise AssertionError('Worker timeout')
        assert not errors,errors
    window.open_path(str(source));idle();before=copy.deepcopy(window.p)
    legacy=root/'legacy.jpg';model.export_ultrahdr(legacy,before)
    sdr=core.process(model.engine.STATE['full'],model.engine.prepare_params(model.params(before)),'srgb')
    base=np.asarray(Image.open(legacy))
    assert np.max(np.abs(base[32,80].astype(float)-np.rint(sdr[32,80]*255)))<=4
    for name,button in window.hdr_preset_buttons.items():
        window.commit_history();index=window.history_index;old=copy.deepcopy(window.p)
        button.click();idle()
        assert window.history_index==index+1
        for key,value in hdr_color.PRESETS[name].items():assert window.p[key]==value
        selected=copy.deepcopy(window.p)
        assert model.recipe_params(model.recipe(selected))==model.params(selected).__dict__
        project=root/(name+'.filmproj');model.save_project(project,selected)
        assert model.read_project(project)['frames']['0']['hdr_strength']==selected['hdr_strength']
        window.travel(-1);idle();assert window.p==old
    assert window.p==before
    window.hdr_preset_buttons['natural'].click();idle();value=copy.deepcopy(window.p)
    jf='Ultra HDR JPEG - SDR + Gain Map (*.jpg *.jpeg)'
    for active in (False,True):
        window.hdr_canvas.active=active
        dest=root/('toolbar-'+str(active)+'.tif')
        try:
            with patch.object(ui.QFileDialog,'getSaveFileName',return_value=(str(dest),jf)) as dialog:
                window.export_button.click();idle();assert jf in dialog.call_args.args[3]
        finally:window.hdr_canvas.active=False
        dest=dest.with_suffix('.jpg');data=dest.read_bytes();assert api.ultrahdr_check(data)
        assert window.last_export==str(dest) and window.p==value
        p=model.engine.prepare_params(model.params(value));linear=hdr_color.render(model.engine.STATE['full'],p)
        hdr,base=hdr_ultrahdr.inputs(linear,p);decoded=api.ultrahdr_decode(data,boost=49).astype(np.float32)
        assert np.allclose(decoded[32,80,:3],hdr[32,80,:3],atol=.03,rtol=.04)
    # Exporting another format with a JPEG suffix still chooses the TIFF encoder.
    with patch.object(ui.QFileDialog,'getSaveFileName',return_value=(str(root/'chosen.jpg'),'HDR TIFF - Float32 (*.tif *.tiff)')):
        window.export_button.click();idle()
    assert (root/'chosen.tif').read_bytes()[:2]==b'II' and not (root/'chosen.jpg').exists()
    with patch.object(ui.QFileDialog,'getSaveFileName',return_value=('',jf)),patch.object(window,'command') as command:
        window.export_dialog();assert not command.called
    window.resize(1280,900);window.tabs.setCurrentIndex(4);app.processEvents()
    window.grab().save(str(folder/'workbench.png'))
    window.loaded=False;window.close();app.processEvents()
(folder/'results.json').write_text(json.dumps({'status':'pass','codec':api.ultrahdr_version(),'ISO_gainmap':True,'SDR_base_with_ICC':True,'HDR_decode_vs_master':True,'content_capacity':True,'odd_dimensions':True,'srgb_and_prophoto':True,'presets_undo_project_recipe':True,'toolbar_format_routing':True,'device_visual_acceptance':False},indent=2),'utf-8')
print('PASS three saved HDR presets/undo/projects/recipes; real ISO Ultra HDR JPEG, sRGB base ICC, SDR/HDR decode, content capacity, ProPhoto, odd sizes, cancellation, atomic no-overwrite and toolbar routing')
