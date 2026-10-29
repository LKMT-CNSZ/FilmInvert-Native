"""Independent radiance reconstruction, endpoint recovery, alignment and float TIFF."""
from pathlib import Path
import os,sys,tempfile,threading
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'vendor'))
import numpy as np
import hdr_io,hdr_merge,hdr_color,core
from tiffio import write_tiff_rgb16
from tiffread import read_tiff_linear
from loader import load

folder=ROOT/'build/hdr-tests';folder.mkdir(exist_ok=True,parents=True)
with tempfile.TemporaryDirectory(dir=folder) as tmp:
    root=Path(tmp);y,x=np.mgrid[:120,:180]
    radiance=np.stack([.02+3*x/180,.03+1.8*y/120,.04+(x+y)/100],axis=-1).astype(np.float32)
    paths=[];evs=[-2,0,2]
    for i,ev in enumerate(evs):
        path=root/f'exposure-{i}.tif';write_tiff_rgb16(path,np.clip(radiance*2.**ev,0,1),with_icc=False);paths.append(path)
    dest=root/'merged.tif';r=hdr_merge.merge(paths,evs,dest,kind='positive',align=False,deghost=False)
    actual=read_tiff_linear(dest)
    assert actual.max()>2.5 and np.max(np.abs(actual-radiance))<.00015,np.max(np.abs(actual-radiance))
    loaded=load(str(dest));assert loaded.hdr_kind=='positive' and np.array_equal(loaded.linear,actual)
    assert len(hdr_io.metadata(dest)['sources'])==3
    # Alignment direction is verified against an independent spatial shift.
    rng=np.random.default_rng(20);texture=rng.uniform(.1,.7,(128,192,3)).astype(np.float32)
    moved=np.roll(np.roll(texture,3,axis=0),-4,axis=1)
    assert hdr_merge.shift(texture,moved)==(-3,4),hdr_merge.shift(texture,moved)
    texture=rng.uniform(.1,.7,(811,933,3)).astype(np.float32)
    moved=np.roll(np.roll(texture,3,axis=0),-4,axis=1)
    assert hdr_merge.shift(texture,moved)==(-3,4),hdr_merge.shift(texture,moved)
    for space in ('srgb','prophoto'):
        path=root/(space+'.tif');hdr_io.write(path,radiance,space)
        assert np.array_equal(read_tiff_linear(path),radiance)
        assert hdr_io.metadata(path)['space']==space
        assert np.max(np.abs(hdr_color.decode(hdr_color.encode(radiance,space),space)-radiance))<2e-6
    p=core.Params(mode='positive',scene_hdr=True,black=0,white=1)
    assert np.max(np.abs(hdr_color.render(radiance,p)-radiance))<2e-6
    assert hdr_color.render(radiance,core.Params(mode='positive',scene_hdr=True,black=0,white=1,exposure=1)).max()>5
    p.local_edits=[dict(kind='gradient',points=[[0,0],[1,0]],ev=1,opacity=1)]
    assert hdr_color.render(radiance,p)[-1,-1,0]>radiance[-1,-1,0]*1.99
    negative=root/'negative-hdr.tif';hdr_io.write(negative,np.clip(radiance*.2,.01,.9),'srgb','negative')
    import desktop_model as model
    reopened=model.open_image(str(negative),input_mode='single')
    assert reopened['params']['mode']=='color_negative' and reopened['params']['scene_hdr'] is True
    canceled=threading.Event();canceled.set()
    try:hdr_merge.merge(paths,evs,root/'cancel.tif',cancel=canceled)
    except InterruptedError:pass
    else:raise AssertionError('Cancellation ignored')
    assert not (root/'cancel.tif').exists()
    before=dest.read_bytes()
    try:hdr_io.write(dest,radiance)
    except FileExistsError:pass
    else:raise AssertionError('Existing source overwritten')
    assert dest.read_bytes()==before
print('PASS physical radiance/endpoints, integer alignment, ICC-tagged float32 headroom roundtrip, HDR local EV, cancellation and existing-file protection')
