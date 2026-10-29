"""HDR transfer units, shoulder, letterboxing and optional native DXGI capability probe."""
from pathlib import Path
import sys,os,json
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'vendor'))
import numpy as np
import hdr_display

image=np.ones((20,40,3),np.float32)
a=hdr_display.scrgb_frame(image,80,80,203,1000)
assert a.dtype==np.float16 and a.shape==(80,80,4)
assert np.all(a[:20,:,:3]==0) and np.all(a[60:,:,:3]==0)
assert abs(float(a[40,40,0])-203/80)<.002 and np.all(a[...,3]==1)
bright=hdr_display.scrgb_frame(image*8,40,20,203,1000)
assert bright[10,20,0]>1 and bright[10,20,0]<1000/80
for invalid in (0,26.0674,float('nan'),float('inf'),20000):
    estimated=hdr_display.scrgb_frame(image*8,40,20,203,invalid)
    assert np.array_equal(estimated,bright),'Bad display metadata must not crush HDR highlights to SDR'
for valid in (80,203,400,1000,10000):assert hdr_display.display_peak(valid)==valid
assert hdr_display.SwapDesc.__sizeof__ is not None
if '--native' in sys.argv:
    if os.name!='nt':raise RuntimeError('Native capability test requires Windows')
    from PySide6.QtWidgets import QApplication,QWidget
    from PySide6.QtCore import Qt
    app=QApplication([]);widget=QWidget();widget.setAttribute(Qt.WidgetAttribute.WA_NativeWindow);widget.resize(64,64)
    surface=hdr_display.Surface(int(widget.winId()),64,64)
    try:
        info=surface.capabilities()
        if info['hdr_active']:
            # This is a hidden test surface; color appearance still needs hardware visual QA.
            surface.present(image,64,64)
            prepared=hdr_display.scrgb_frame(image*2,64,64)
            texture=surface.texture.value
            for _ in range(3):surface.present_prepared(prepared,64,64);assert surface.texture.value==texture
            # Read back the GPU upload, including repeated UpdateSubresource calls.
            C=hdr_display.C
            class Mapped(C.Structure):_fields_=[('data',C.c_void_p),('row',C.c_uint32),('depth',C.c_uint32)]
            stage=hdr_display.P();desc=hdr_display.TextureDesc(64,64,1,1,10,hdr_display.Sample(1,0),3,0,0x20000,0)
            hdr_display.check(hdr_display.call(surface.device,5,hdr_display.I,[C.POINTER(hdr_display.TextureDesc),hdr_display.P,C.POINTER(hdr_display.P)],C.byref(desc),None,C.byref(stage)),'Create readback')
            try:
                hdr_display.call(surface.context,47,None,[hdr_display.P,hdr_display.P],stage,surface.texture)
                mapped=Mapped()
                hdr_display.check(hdr_display.call(surface.context,14,hdr_display.I,[hdr_display.P,hdr_display.U,hdr_display.U,hdr_display.U,C.POINTER(Mapped)],stage,0,1,0,C.byref(mapped)),'Map readback')
                try:
                    actual=np.stack([np.frombuffer(C.string_at(mapped.data+y*mapped.row,64*8),np.float16).reshape(64,4) for y in range(64)])
                    assert np.array_equal(actual,prepared)
                finally:hdr_display.call(surface.context,15,None,[hdr_display.P,hdr_display.U],stage,0)
            finally:hdr_display.release(stage)
            surface.present_prepared(hdr_display.scrgb_frame(image,96,48),96,48)
            surface.present(image,64,64)
        else:
            try:surface.present(image,64,64)
            except RuntimeError as e:assert 'HDR enabled' in str(e)
            else:raise AssertionError('SDR output silently presented as HDR')
        evidence=dict(native_dxgi=True,fp16_swapchain_created=True,display=info,prepared_texture_update_and_GPU_readback=bool(info['hdr_active']),visual_hdr_appearance_validated=False)
        dest=ROOT/'build/hdr-tests/native-display.json';dest.parent.mkdir(parents=True,exist_ok=True);dest.write_text(json.dumps(evidence,indent=2),'utf-8')
        print(json.dumps(evidence,indent=2))
    finally:surface.close();surface.close();widget.close()
print('PASS scRGB 80-nit unit conversion, reference white, bounded highlight shoulder and alpha/letterbox')
