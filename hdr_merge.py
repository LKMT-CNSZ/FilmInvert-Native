"""Linear exposure merging, bounded sequential decoding and translation alignment."""
from pathlib import Path
import hashlib
import numpy as np
import hdr_io

MAX_PIXELS=20_000_000


def shift(reference,image):
    """Phase correlation at bounded resolution; returns integer source translation."""
    h,w=reference.shape[:2];step=max(1,int(np.ceil(max(h,w)/768)))
    def sampled(value):
        # Area-average before decimation, so odd-pixel shifts do not alias away
        # the texture used by the bounded FFT. Scratch is only one source band.
        positions=np.arange(0,w,step);counts=np.minimum(step,w-positions)
        rows=[]
        for y in range(0,h,step):
            line=value[y:y+step].mean(axis=(0,2))
            rows.append(np.add.reduceat(line,positions)/counts)
        return np.log1p(np.asarray(rows)*32)
    a=sampled(reference);b=sampled(image)
    a=a-a.mean();b=b-b.mean()
    if a.std()<1e-5 or b.std()<1e-5:return 0,0
    window=np.hanning(a.shape[0])[:,None]*np.hanning(a.shape[1])[None,:]
    fa=np.fft.rfft2(a*window);fb=np.fft.rfft2(b*window)
    cross=fa*np.conj(fb);cross/=np.maximum(np.abs(cross),1e-9)
    corr=np.fft.irfft2(cross,s=a.shape).real
    yy,xx=np.unravel_index(np.argmax(corr),corr.shape)
    if yy>corr.shape[0]//2:yy-=corr.shape[0]
    if xx>corr.shape[1]//2:xx-=corr.shape[1]
    dy,dx=int(yy*step),int(xx*step)
    if step>1:
        # Refine in full-resolution pixels: coarse FFT samples alone would only
        # return multiples of the downsampling step on normal camera frames.
        stride=max(1,int(np.ceil(max(h,w)/160)))
        margin=step+max(abs(dy),abs(dx))+1
        yy,xx=np.mgrid[margin:h-margin:stride,margin:w-margin:stride]
        if yy.size>=64:
            ref=reference[yy,xx].mean(axis=2);best=(float('inf'),dy,dx)
            for cy in range(dy-step,dy+step+1):
                for cx in range(dx-step,dx+step+1):
                    candidate=image[yy-cy,xx-cx].mean(axis=2)
                    valid=(ref>.02)&(ref<.95)&(candidate>.02)&(candidate<.95)
                    if valid.sum()<64:continue
                    residual=np.log2(candidate[valid]/ref[valid]);residual-=np.median(residual)
                    score=float(np.median(np.abs(residual)))
                    if score<best[0]:best=(score,cy,cx)
            _,dy,dx=best
    if abs(dy)>h*.05 or abs(dx)>w*.05:raise ValueError('Alignment exceeds 5%; register the exposures before merging')
    return dy,dx


def decode(path,space,box=None,rotation=0):
    from loader import load
    from native_efficiency import estimate_decoded_bytes
    t=hdr_io.tags(path)
    if box is not None and t and t.get(259)==1 and t.get(277)==3 and t.get(284,1)==1 and t.get(278,0)>=t[257]:
        bits=t.get(258);bits=bits[0] if isinstance(bits,tuple) else bits
        if bits!=16:raise ValueError('Frame-region merging requires uncompressed 16-bit RGB scans')
        x0,y0,x1,y1=map(int,box);h,w=t[257],t[256]
        if not 0<=x0<x1<=w or not 0<=y0<y1<=h:raise ValueError('Selected frame does not fit all scan files')
        if (x1-x0)*(y1-y0)>MAX_PIXELS:raise ValueError('Merge frame exceeds the 20 MP memory budget')
        offsets=t[273];offset=offsets[0] if isinstance(offsets,tuple) else offsets
        from file_io import io_path
        mapped=np.memmap(io_path(path),mode='r',dtype=t['order']+'u2',offset=offset,shape=(h,w,3))
        x=mapped[y0:y1,x0:x1].astype(np.float32)/65535
        del mapped
        if rotation:x=np.rot90(x,-rotation//90).copy()
        # Scan-region mode explicitly uses linear sRGB, like legacy scanner input.
        if space!='srgb':
            import working_color
            x=working_color.linear(x,'srgb',space)
        return x
    if box is not None:raise ValueError('Frame-region mode needs same-layout uncompressed RGB TIFF/FFF scans')
    estimate=estimate_decoded_bytes(path)
    if estimate and estimate>MAX_PIXELS*12:raise ValueError('Input exceeds the 20 MP merge budget; select a scan frame or pre-crop it')
    loaded=load(str(path),working_space=space)
    if loaded.width*loaded.height>MAX_PIXELS:raise ValueError('Input exceeds the 20 MP merge budget')
    return loaded.linear


def merge(paths,evs,destination,kind='negative',space='srgb',align=True,deghost=True,box=None,rotation=0,cancel=None,progress=lambda *a:None):
    paths=[Path(p) for p in paths]
    evs=np.asarray(evs,dtype=np.float64)
    if not 2<=len(paths)<=9 or len(evs)!=len(paths) or not np.isfinite(evs).all() or np.max(np.abs(evs))>8 or np.ptp(evs)<.5:raise ValueError('Choose 2–9 exposures of the same image, with distinct relative EV values within ±8')
    resolved=[str(p.resolve()) for p in paths]
    if len(set(resolved))!=len(paths):raise ValueError('An exposure file was selected more than once')
    if str(Path(destination).resolve()) in resolved:raise ValueError('Output cannot replace an exposure source')
    def check():
        if cancel is not None and cancel.is_set():raise InterruptedError('Exposure merge canceled')
    # A middle exposure is used for alignment and deghosting only.
    order=np.argsort(np.abs(evs-np.median(evs))).tolist();reference=None;total=None;weight_sum=None;records=[];overlap=None
    for number,index in enumerate(order):
        check();path=paths[index];before=path.stat()
        with path.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
        x=decode(path,space,box,rotation);check()
        if reference is None:
            h,w=x.shape[:2];reference=x.copy();reference_ev=float(evs[index]);total=np.zeros_like(x);weight_sum=np.zeros((h,w),np.float32);overlap=[0,0,w,h]
        if x.shape!=reference.shape:raise ValueError('All exposures must have identical dimensions')
        dy,dx=shift(reference,x) if align and number else (0,0)
        left,top,right,bottom=max(0,dx),max(0,dy),min(w,w+dx),min(h,h+dy)
        overlap=[max(overlap[0],left),max(overlap[1],top),min(overlap[2],right),min(overlap[3],bottom)]
        ghost_pixels=0
        for y in range(top,bottom,128):
            check();end=min(y+128,bottom)
            sample=x[y-dy:end-dy,left-dx:right-dx]
            # Exclude sensor endpoints in every color channel, preserving color ratios.
            confidence=np.minimum(sample.min(axis=2)/.15,(1-sample.max(axis=2))/.15)
            weight=np.clip(confidence,0,1).astype(np.float32)
            weight[(sample.min(axis=2)<=.002)|(sample.max(axis=2)>=.998)]=0
            radiance=sample*np.float32(2.**(-evs[index]))
            if deghost and number:
                ref=reference[y:end,left:right];ref_r=ref*np.float32(2.**(-reference_ev))
                reliable=(ref.min(axis=2)>.02)&(ref.max(axis=2)<.95)&(sample.min(axis=2)>.02)&(sample.max(axis=2)<.95)
                difference=np.max(np.abs(np.log2(np.maximum(radiance,1e-5)/np.maximum(ref_r,1e-5))),axis=2)
                moving=reliable&(difference>.65);weight[moving]=0;ghost_pixels+=int(moving.sum())
            total[y:end,left:right]+=radiance*weight[...,None]
            weight_sum[y:end,left:right]+=weight
        after=path.stat()
        if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('An exposure changed while merging')
        records.append(dict(path=str(path.resolve()),sha256=digest,ev=float(evs[index]),translation=[dx,dy],deghost_rejected_pixels=ghost_pixels))
        progress(number+1,len(paths),path.name)
        del x
    left,top,right,bottom=overlap
    if right<=left or bottom<=top:raise ValueError('Exposures have no common aligned region')
    missing=weight_sum<=1e-8
    # Unrecoverable endpoints retain the reference; never invent scene detail.
    total/=np.maximum(weight_sum,1e-8)[...,None]
    total[missing]=reference[missing]*np.float32(2.**(-reference_ev))
    output=total[top:bottom,left:right]
    scale=1.
    if kind=='negative':
        # Film transmission has an arbitrary common exposure scale. Keep it below 1
        # before base subtraction; ratios and density differences remain unchanged.
        scale=.95/max(float(output.max()),1e-6);output=output*np.float32(scale)
    info=dict(sources=records,alignment='integer translation' if align else 'none',
        common_region=overlap,scan_region=box,scan_rotation=rotation,scale=scale,
        unrecovered_fraction=float(missing[top:bottom,left:right].mean()),deghost=deghost)
    check();hdr_io.write(destination,output,space,kind,info,cancel)
    return {'path':str(destination),'metadata':info,'shape':list(output.shape)}
