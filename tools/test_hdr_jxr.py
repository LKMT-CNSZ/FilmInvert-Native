"""Native Windows HDR JPEG XR roundtrip, absolute brightness and output safety."""
from pathlib import Path
import sys,tempfile,threading,concurrent.futures,hashlib
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'vendor'))
import numpy as np,hdr_jxr,working_color
folder=ROOT/'build/jxr-tests';folder.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(dir=folder) as tmp:
    root=Path(tmp);rng=np.random.default_rng(7)
    a=rng.uniform(0,5,(43,69,3)).astype(np.float32);a[0,0]=1;a[0,1]=0;a[0,2]=[8,.5,.1]
    original=hashlib.sha256(a.tobytes()).hexdigest()
    for space in ('srgb','prophoto'):
        path=root/(space+' 中文 空格.jxr');hdr_jxr.write(path,a,space)
        actual=hdr_jxr.read_scrgb(path);expected=working_color.linear(a,space,'srgb')*np.float32(203/80)
        assert actual.shape==a.shape and actual.dtype==np.float32 and actual.max()>1
        assert np.max(np.abs(actual-expected))<max(.003,float(np.max(np.abs(expected)))*.0001)
        assert np.max(np.abs(actual[0,0]-203/80))<.001
        before=path.read_bytes()
        try:hdr_jxr.write(path,a)
        except FileExistsError:pass
        else:raise AssertionError('Existing output overwritten')
        assert path.read_bytes()==before
    for bad in (a*np.nan,a[:0]):
        try:hdr_jxr.write(root/'bad.jxr',bad)
        except ValueError:pass
        else:raise AssertionError('Invalid pixels accepted')
        assert not (root/'bad.jxr').exists()
    event=threading.Event();event.set()
    try:hdr_jxr.write(root/'cancel.jxr',a,cancel=event)
    except InterruptedError:pass
    else:raise AssertionError('Cancellation ignored')
    assert not (root/'cancel.jxr').exists() and not list(root.glob('*.part'))
    def worker(i):
        # Qt GUI callers can already be in an STA. CoInitializeEx must neither
        # uninitialize that apartment nor leak its own MTA initialization.
        import ctypes as C
        ole=C.WinDLL('ole32');ole.CoInitializeEx(None,2)
        try:
            path=root/f'worker-{i}.jxr';hdr_jxr.write(path,a);assert hdr_jxr.read_scrgb(path).shape==a.shape
        finally:ole.CoUninitialize()
    with concurrent.futures.ThreadPoolExecutor(2) as pool:list(pool.map(worker,range(4)))
    assert hashlib.sha256(a.tobytes()).hexdigest()==original
print('PASS native JPEG XR float HDR units/primaries/dimensions, Unicode output, finite validation, cancellation, overwrite protection and worker/STA COM lifetime')
