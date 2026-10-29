"""Native desktop adapter. Calls the validated processing functions directly.

No HTTP server, browser, upload, or listening port is started by this module.
"""
from pathlib import Path
import copy
import json
import time
import uuid
import hashlib
import os
from contextlib import contextmanager
import numpy as np
import core
import server as engine
import strip
import restoration
import calibration
import geometry
from loader import load
from file_io import io_path,temporary_path

VERSION = '3.1.0-rc12'
from preview_cache import PreviewCache
from document_cache import DocumentCache,BlobCache
PREVIEWS=PreviewCache()
DOCUMENTS=DocumentCache() # GUI opts in; serial preparation/export workers stay uncached.
NAV_THUMBS=BlobCache()

def clone(value):
    return copy.deepcopy(value)

def params(value):
    return engine.params_from(value)

@contextmanager
def preserve_document_on_error():
    # Arrays are immutable inputs here; retaining their references avoids copying a roll.
    with engine.LOCK:
        saved=dict(engine.STATE)
        saved['info']=clone(saved['info'])
        saved['frame_params']=clone(saved['frame_params'])
        thumbs=dict(engine.THUMB_CACHE);flat=dict(engine.FLAT_CACHE)
    try:
        yield
    except Exception:
        with engine.LOCK:
            engine.STATE.clear();engine.STATE.update(saved)
            engine.THUMB_CACHE.clear();engine.THUMB_CACHE.update(thumbs)
            engine.FLAT_CACHE.clear();engine.FLAT_CACHE.update(flat)
        raise

def source_identity(path):
    path=Path(path);physical=io_path(path);before=physical.stat()
    with physical.open('rb') as stream:
        digest=hashlib.file_digest(stream,'sha256').hexdigest()
    after=physical.stat()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
        raise ValueError('原图在读取过程中发生变化，请稍后重试')
    return {'path':str(path.resolve()),'size':after.st_size,'sha256':digest,'mtime_ns':after.st_mtime_ns}

def clear_navigation_caches():
    import viewport_render
    DOCUMENTS.clear();NAV_THUMBS.clear();PREVIEWS.entries.clear();PREVIEWS.bytes=0;viewport_render.clear_context()

def _initial_analysis():
    return {'info':clone(engine.STATE['info']),'params':clone(engine.STATE['params'].__dict__),
            'preview':engine.STATE['preview'],'layout':clone(engine.STATE['strip_layout']),
            'input':clone(engine.STATE['native_input'])}

def _install_analysis(path,loaded,state):
    layout=state['layout'];full=strip.extract_one(loaded.linear,layout,0)[2] if layout is not None else loaded.linear
    with engine.LOCK:
        engine.FLAT_CACHE.clear();engine.THUMB_CACHE.clear()
        engine.STATE.update(path=path,native_loaded=loaded,native_input=state['input'],frame_params={},roll_params=None,
            auto_meter=True,strip_img=loaded.linear if layout is not None else None,strip_layout=layout,frame_index=0,
            full=full,preview=state['preview'],params=params(state['params']),info=state['info'])
    return engine.STATE['info']

def open_image(path,identity=None,include_thumbnails=True,input_mode='auto',manual=None,input_color='legacy',working_space='srgb'):
    import viewport_render
    viewport_render.clear_context()
    with preserve_document_on_error():
        identity=identity or source_identity(path)
        cached=engine.STATE.get('native_loaded');previous=engine.STATE.get('native_source') or {}
        reusable=(cached is not None and cached.color==input_color and cached.working_space==working_space
                  and all(previous.get(k)==identity.get(k) for k in ('path','size','sha256','mtime_ns')))
        key=DOCUMENTS.key(identity,input_color,working_space)
        decoded=cached if reusable else DOCUMENTS.get(key)
        if decoded is None:decoded=load(identity['path'],input_color,working_space)
        if input_mode not in ('auto','single','strip'):raise ValueError('输入模式无效')
        if manual is not None:
            if input_mode!='strip':raise ValueError('手动选框需要整条输入模式')
            manual=strip.normalize_manual(manual,decoded.linear.shape)
        mode=json.dumps([input_mode,manual],sort_keys=True)
        initial=DOCUMENTS.get_analysis(key,mode)
        info=clone(_install_analysis(identity['path'],decoded,initial) if initial is not None else engine.install_loaded(identity['path'],decoded,input_mode,manual))
        stat=io_path(identity['path']).stat()
        if (stat.st_size,stat.st_mtime_ns)!=(identity['size'],identity['mtime_ns']):
            raise ValueError('原图在读取过程中发生变化，请稍后重试')
        if initial is None:
            # Replacing an existing entry would discard its other input modes.
            if key not in DOCUMENTS.entries:DOCUMENTS.put(key,decoded)
            DOCUMENTS.put_analysis(key,mode,_initial_analysis())
        p = clone(engine.STATE['params'].__dict__)
        p['channel_repair'] = bool(info.get('channel_health',{}).get('suggest'))
        with engine.LOCK:engine.STATE['native_source']=identity
        return {'info':info,'params':p,'thumbs':thumbnails() if include_thumbnails else []}

def thumbnails():
    layout = engine.STATE['strip_layout']
    if layout is None:
        return []
    original_thumbnails()
    result=[]
    for index in range(layout.frame_count):
        key=json.dumps([engine.STATE.get('native_source'),engine.STATE.get('native_input'),layout.__dict__,index,
                        engine.STATE['frame_params'].get(index),engine.STATE['roll_params'],engine.STATE['auto_meter']],sort_keys=True)
        value=NAV_THUMBS.get(key)
        if value is None:value=engine.do_thumb(index);NAV_THUMBS.put(key,value)
        else:
            with engine.LOCK:engine.THUMB_CACHE[index]=value
        result.append(value)
    return result

def original_thumbnails():
    """Cached unprocessed strip frames; never install frame parameters or meter."""
    layout=engine.STATE['strip_layout']
    if layout is None:return []
    import colorio
    result=[];space=engine.STATE.get('native_input',{}).get('working_space','srgb')
    for index in range(layout.frame_count):
        key=json.dumps(['original',engine.STATE.get('native_source'),engine.STATE.get('native_input'),layout.__dict__,index],sort_keys=True)
        value=NAV_THUMBS.get(key)
        if value is None:
            arr=engine.downscale(strip.extract_one(engine.STATE['strip_img'],layout,index)[2],320)
            encoded=core.srgb_encode(arr) if space=='srgb' else colorio.encode(arr,space)
            value=engine.encode_preview(colorio.convert(encoded,'srgb',space));NAV_THUMBS.put(key,value)
        result.append(value)
    return result

def preview(value, full=False, original=False, region=None, cancel=None):
    p = engine.prepare_params(params(value))
    lin = engine.STATE['full' if full else 'preview']
    if lin is None:
        raise ValueError('请先打开图像')
    if full and region is not None:
        import viewport_render,colorio
        wide,box,shape,method=viewport_render.render(lin,p,region,original,cancel)
        out=colorio.convert(wide,'srgb',p.working_space)
        diagnostics=colorio.output_diagnostics(wide,p.output_space,p.working_space)
        result={'pixels':(np.clip(out,0,1)*255+.5).astype(np.uint8),'hist':diagnostics['hist'],
                'params':p.__dict__,'diagnostics':diagnostics,'region':box,'image_shape':shape,'region_method':method}
        if p.working_space=='prophoto':result['wide']=wide
        return result
    # Bound interactive full-size processing, which creates multiple float arrays.
    if full and lin.shape[0]*lin.shape[1]*96>768*1024*1024:
        raise ValueError('该图像的全尺寸预览预计超过 768 MB 临时内存预算，请使用适应窗口预览；全尺寸 TIFF 导出仍可使用')
    key=json.dumps([engine.STATE.get('native_source'),engine.STATE.get('native_input'),source_region(),p.__dict__,full,original],sort_keys=True)
    cached=PREVIEWS.get(key) if p.working_space=='srgb' else None
    if cached is not None:
        with engine.LOCK:
            engine.STATE['params']=p
            engine.STATE['frame_params'][engine.STATE['frame_index']]=clone(p.__dict__)
            engine.THUMB_CACHE.pop(engine.STATE['frame_index'],None)
        return {'pixels':cached[0],'hist':cached[1],'params':p.__dict__,'diagnostics':cached[3]}
    if original:
        import colorio
        raw=lin[:,::-1] if p.flip else lin
        wide=geometry.apply(core.srgb_encode(raw).astype(np.float32) if p.working_space=='srgb' else colorio.encode(raw,p.working_space),p.__dict__)
        out=colorio.convert(wide,'srgb',p.working_space)
    else:
        wide=core.process(lin,p,target_space=p.working_space)
        import colorio
        out=colorio.convert(wide,'srgb',p.working_space)
    with engine.LOCK:
        engine.STATE['params'] = p
        engine.STATE['frame_params'][engine.STATE['frame_index']] = clone(p.__dict__)
        engine.THUMB_CACHE.pop(engine.STATE['frame_index'],None)
    pixels = (np.clip(out,0,1)*255+.5).astype(np.uint8)
    diagnostics=colorio.output_diagnostics(wide,p.output_space,p.working_space)
    # Histogram measures selected output encoding, never monitor RGB.
    sample = pixels[::max(1,pixels.shape[0]//640),::max(1,pixels.shape[1]//640)]
    hist = [np.bincount(sample[...,c].ravel(),minlength=256) for c in range(3)]
    hist=diagnostics['hist']
    if not full and p.working_space=='srgb':PREVIEWS.put(key,pixels,hist,diagnostics)
    result={'pixels':pixels,'hist':hist,'params':p.__dict__,'diagnostics':diagnostics}
    if p.working_space=='prophoto':result['wide']=wide
    return result

def change_frame(index,value,keep=True,meter=True):
    with engine.LOCK:engine.THUMB_CACHE.pop(engine.STATE['frame_index'],None)
    result = engine.do_frame(index,value,keep,meter)
    return {'info':clone(engine.STATE['info']),'params':clone(result['params'])}

def sample(value,x,y,kind,full=False):
    # Match the geometry of the image actually displayed, including rounding at
    # preview resolution, before sampling the full-resolution source.
    x,y = geometry.source_point(x,y,engine.STATE['full' if full else 'preview'].shape,value)
    result = engine.do_sample(x,y,kind,value)
    return clone(result['params'])


def geometry_preview(value):
    p = engine.prepare_params(params(value))
    p.rotation,p.straighten,p.crop = 0,0.,geometry.FULL.copy()
    p.perspective=None
    wide = core.process(engine.STATE['preview'],p,target_space=p.working_space)
    import colorio
    out=colorio.convert(wide,'srgb',p.working_space)
    return {'working_pixels':wide,'working_space':p.working_space,'pixels':(np.clip(out,0,1)*255+.5).astype(np.uint8),
            'source_shape':engine.STATE['full'].shape[:2]}


def normalize_input(value):
    if not isinstance(value,dict) or value.get('mode') not in ('auto','single','strip'):
        raise ValueError('工程输入模式无效')
    manual=value.get('manual')
    if manual is not None:
        if value['mode']!='strip':raise ValueError('手动选框需要整条输入模式')
        manual=strip.normalize_manual(manual)
    result={'mode':value['mode'],'manual':manual}
    if value.get('working_space','srgb') not in ('srgb','prophoto'):raise ValueError('工程工作空间无效')
    if value.get('working_space')=='prophoto':result['working_space']='prophoto'
    if 'color' in value:
        from input_color import CHOICES
        if value['color'] not in CHOICES:raise ValueError('工程输入色彩无效')
        if value['color']!='legacy':result['color']=value['color']
    return result


def layout_rows(layout=None):
    manual=engine.STATE.get('native_input',{}).get('manual')
    if layout is None and manual is not None:return clone(manual['frames'])
    layout=engine.STATE['strip_layout'] if layout is None else layout
    if layout is None:
        h,w=engine.STATE['native_loaded'].linear.shape[:2]
        return [{'id':'frame-0','box':[0,0,w,h]}]
    return [{'id':f'frame-{i}','box':[x0,y0,x1,y1]}
            for i,(_,_,x0,x1,y0,y1) in enumerate(strip.frame_boxes(layout))]


def input_geometry(spec):
    return bool(spec.get('manual') and any(row.get('angle') or 'base' in row for row in spec['manual']['frames']))


def layout_editor():
    lin=engine.STATE['native_loaded'].linear
    layout=engine.STATE['strip_layout']
    detected=strip.detect_layout(lin)
    h,w=lin.shape[:2]
    detected_rows=layout_rows(detected) if detected.frame_count else [{'id':'frame-0','box':[0,0,w,h]}]
    from working_color import display
    small=engine.downscale(lin,1600)
    shown=core.srgb_encode(small) if engine.STATE['native_loaded'].working_space=='srgb' else display(small,'prophoto')
    import colorio
    from strip_alignment import suggest
    return {'working_pixels':colorio.encode(small,engine.STATE['native_loaded'].working_space),'working_space':engine.STATE['native_loaded'].working_space,'pixels':(shown*255+.5).astype(np.uint8),
            'width':w,'height':h,'frames':layout_rows(),
            'rotation':layout.rotation if layout else 0,
            'input':clone(engine.STATE['native_input']),
            'detected_frames':detected_rows,'detected_rotation':detected.rotation,
            'detected_count':detected.frame_count,'alignment_candidates':suggest(lin,layout_rows())}


def source_region(index=None):
    layout=engine.STATE['strip_layout']
    if layout is None:return None
    index=engine.STATE['frame_index'] if index is None else index
    si,fi,x0,x1,y0,y1=strip.frame_boxes(layout)[index]
    rows=layout_rows()
    result={'box':[x0,y0,x1,y1],'rotation':layout.rotation,'id':rows[index]['id']}
    if strip.frame_angle(layout,index):result['angle']=strip.frame_angle(layout,index)
    return result


def calibration_signature(p):
    signature=calibration.parameter_signature(p.__dict__)
    if engine.STATE.get('native_input',{}).get('manual') is not None:
        signature=hashlib.sha256(json.dumps([signature,source_region()],sort_keys=True).encode()).hexdigest()
    return signature


def _restore_edits(data):
    layout=engine.STATE['strip_layout'];count=layout.frame_count if layout else 1
    if count!=data['frame_count'] or layout_identity()!=data.get('layout'):
        raise ValueError('胶片布局与工程记录不一致，已保留当前编辑')
    index=data['current_frame'];saved={int(k):clone(v) for k,v in data['frames'].items()}
    if index:engine.do_frame(index,{},False,False)
    p=engine.prepare_params(params(saved[index]))
    with engine.LOCK:
        engine.STATE.update(frame_params=saved,roll_params=clone(data.get('roll_params')),params=p,
                            auto_meter=data.get('options',{}).get('auto_meter',True),
                            default_meter_mode=snapshot_meter_mode(data))
        engine.STATE['info'].update(base=p.base,anchor=p.anchor,span=p.span)
        engine.THUMB_CACHE.clear()
    return {'info':clone(engine.STATE['info']),'params':clone(p.__dict__),'thumbs':thumbnails()}


def restore_layout(snapshot):
    with preserve_document_on_error():
        if snapshot['source']['sha256']!=engine.STATE['native_source']['sha256']:
            raise ValueError('切帧备份不属于当前原图')
        spec=normalize_input(snapshot['input'])
        engine.install_loaded(engine.STATE['path'],engine.STATE['native_loaded'],spec['mode'],spec['manual'])
        _restore_legacy_detection(snapshot)
        return _restore_edits(snapshot)


def _restore_legacy_detection(data):
    """Old auto projects retain their exact, recorded boxes after an upgrade.

    Only accept a layout reproduced by the old detector; arbitrary or damaged
    project coordinates must still fail with the existing rollback behavior.
    """
    if layout_identity()==data.get('layout'):return
    spec=data['input']
    if spec.get('manual') is not None or spec['mode']=='single':return
    legacy=strip.detect_layout_legacy(engine.STATE['native_loaded'].linear)
    active=spec['mode']=='strip' or legacy.frame_count>1
    identity={'boxes':[list(v) for v in strip.frame_boxes(legacy)],'rotation':legacy.rotation} if active else None
    count=legacy.frame_count if active else 1
    if identity!=data.get('layout') or count!=data['frame_count']:return
    engine.install_loaded(engine.STATE['path'],engine.STATE['native_loaded'],spec['mode'],None,legacy_detection=True)


def reframe(spec,value,options=None):
    """Commit geometry atomically, preserving edits by stable frame id."""
    spec=normalize_input(spec)
    with preserve_document_on_error():
        before=project_snapshot(value,options)
        old_rows=layout_rows();old_layout=engine.STATE['strip_layout']
        old_rotation=old_layout.rotation if old_layout else 0
        old_map={row['id']:(i,row['box'],row.get('angle',0.)) for i,row in enumerate(old_rows)}
        selected_id=old_rows[engine.STATE['frame_index']]['id']
        if spec.get('manual') and any(row.get('angle') or row.get('base') for row in spec['manual']['frames']):
            for row in spec['manual']['frames']:
                prior=old_map.get(row['id'])
                if prior and prior[1]==row['box'] and 'base' not in row and old_layout is not None:
                    si=strip.frame_boxes(old_layout)[prior[0]][0];row['base']=list(old_layout.bases[si])
        engine.install_loaded(engine.STATE['path'],engine.STATE['native_loaded'],spec['mode'],spec['manual'])
        rows=layout_rows();layout=engine.STATE['strip_layout']
        rotation=layout.rotation if layout else 0
        saved={};changed=0
        for i,row in enumerate(rows):
            prior=old_map.get(row['id']);unchanged=bool(prior and prior[1]==row['box'] and prior[2]==row.get('angle',0.) and rotation==old_rotation)
            original=before['frames'].get(str(prior[0])) if prior else None
            seed=original if original is not None else before['roll_params']
            # Newly added frames inherit current tone; existing untouched frames
            # retain their old default instead of adopting another frame's edits.
            if seed is None and prior is None:seed=value
            p=params(seed or {})
            p.working_space=spec.get('working_space','srgb')
            if layout:
                si,_,arr=strip.extract_one(engine.STATE['strip_img'],layout,i)
                base=layout.bases[si]
            else:
                arr=engine.STATE['full'];base=engine.STATE['params'].base
            if original is None:
                p.base,p.anchor=base,None
                _,p.span=core.auto_anchor(arr,base,p.meter_mode)
            had_lighting=p.flat_mode!='off' or p.vignette>0
            cleared_lighting=False
            if not unchanged:
                changed+=1;p.rotation,p.straighten,p.crop=0,0.,geometry.FULL.copy();p.local_edits=[];p.perspective=None
                p.flat_mode,p.flat_strength,p.flat_field,p.vignette='off',0.,None,0.
                cleared_lighting=had_lighting
            elif (p.flat_mode=='strip' and (spec['manual'] is not None or layout is None)) or (p.flat_mode=='reference' and layout is not None):
                p.flat_mode,p.flat_strength,p.flat_field='off',0.,None
                p.vignette=0.;cleared_lighting=True
            if cleared_lighting:
                # A base measured in corrected illumination cannot be reused as
                # a raw-transmission base after the illumination field is removed.
                p.base,p.anchor=base,None
                _,p.span=core.auto_anchor(arr,base,p.meter_mode)
            saved[str(i)]=p.__dict__
        roll=clone(before['roll_params'])
        if roll is not None:
            if changed:roll.update(local_edits=[],rotation=0,straighten=0.,crop=geometry.FULL.copy(),perspective=None,flat_mode='off',flat_strength=0.,flat_field=None,vignette=0.)
            elif (roll.get('flat_mode')=='strip' and (spec['manual'] is not None or layout is None)) or (roll.get('flat_mode')=='reference' and layout is not None):
                roll.update(flat_mode='off',flat_strength=0.,flat_field=None)
        ids=[row['id'] for row in rows]
        index=ids.index(selected_id) if selected_id in ids else min(before['current_frame'],len(rows)-1)
        data={'frame_count':len(rows),'current_frame':index,'frames':saved,'layout':layout_identity(),
              'roll_params':roll,'options':before['options']}
        result=_restore_edits(data)
        result.update(previous=before,changed=changed)
        return result

def white_balance(value,method):
    return clone(engine.do_wb({'params':value,'method':method})['params'])

def meter(value):
    return clone(engine.do_flat_meter({'params':value})['params'])

def flat_reference(value,path):
    if engine.STATE['strip_layout'] is not None:
        raise ValueError('整条扫描请使用「片基照明估计」，参考平场用于单张图')
    spec=engine.STATE.get('native_input',{})
    ref = load(str(path),spec.get('color','legacy'),spec.get('working_space','srgb')).linear
    if ref.shape != engine.STATE['full'].shape:
        raise ValueError('均匀参考必须与当前原图尺寸和方向相同')
    p = clone(value)
    p.update(flat_mode='reference',flat_strength=1.,flat_field=restoration.reference_field(ref))
    return meter(p)

def calibration_preview(value):
    if value.get('mode')=='bw_negative':raise ValueError('黑白负片不使用彩色色卡校正')
    p=engine.prepare_params(params(value))
    linear=core.working_linear(engine.STATE['preview'],p)
    return {'linear':linear,'working_space':p.working_space,'source_sha256':engine.STATE['native_source']['sha256'],
            'frame':engine.STATE['frame_index'],'parameter_signature':calibration_signature(p)}


def hdr_edit_preview(value,full=False,original=False,region=None,cancel=None):
    import hdr_color,working_color
    p=engine.prepare_params(params(value));source=engine.STATE['full' if full else 'preview']
    if full and source.shape[0]*source.shape[1]*96>768*1024*1024:
        raise ValueError('HDR full-resolution preview exceeds the 768 MB temporary budget; use fit view')
    if cancel is not None and cancel.is_set():raise InterruptedError('HDR preview canceled')
    result=preview(value,full,original,region,cancel)
    if original:
        linear=source[:,::-1] if p.flip else source
        peak=max(1.,float(np.max(linear)))
        linear=geometry.apply(linear/peak,p.__dict__)*np.float32(peak)
    else:linear=hdr_color.render(source,p)
    if cancel is not None and cancel.is_set():raise InterruptedError('HDR preview canceled')
    if 'region' in result:
        x0,y0,x1,y1=result['region'];linear=linear[y0:y1,x0:x1]
    result['hdr_linear']=working_color.linear(linear,p.working_space,'srgb')
    return result


def hdr_preview(value):
    import hdr_color,working_color
    p=engine.prepare_params(params(value))
    linear=hdr_color.render(engine.STATE['preview'],p)
    return working_color.linear(linear,p.working_space,'srgb')


def export_hdr_view(path,value,cancel=None):
    import hdr_color,hdr_jxr
    p=engine.prepare_params(params(value))
    if cancel is not None and cancel.is_set():raise InterruptedError('HDR export canceled')
    linear=hdr_color.render(engine.STATE['full'],p)
    return hdr_jxr.write(path,linear,p.working_space,cancel)


def export_hdr(path,value,cancel=None):
    import hdr_color,hdr_io
    p=engine.prepare_params(params(value))
    if cancel is not None and cancel.is_set():raise InterruptedError('HDR export canceled')
    linear=hdr_color.render(engine.STATE['full'],p)
    hdr_io.write(path,linear,p.working_space,'positive',
        {'source':clone(engine.STATE.get('native_source')),'source_region':source_region(),'recipe':recipe(value),
         'sdr_rolloff_bypassed':True},cancel)
    return str(path)

def fit_calibration(value,reference,points,name):
    if value.get('mode')=='bw_negative':raise ValueError('黑白负片不使用彩色色卡校正')
    p=engine.prepare_params(params(value))
    linear=core.working_linear(engine.STATE['full'],p)
    samples,clipped=calibration.sample_blocks(linear,points)
    result=calibration.fit(reference,samples,clipped,points,name,p.working_space)
    result.update(source_sha256=engine.STATE['native_source']['sha256'],frame=engine.STATE['frame_index'],
                  parameter_signature=calibration_signature(p))
    return result

def apply_all(value,meter_each=True):
    return engine.do_apply_all({'params':value,'auto_meter':meter_each})

def snapshot_meter_mode(snapshot):
    values=[*snapshot['frames'].values(),snapshot.get('roll_params') or {}]
    return 'edges' if any(v.get('meter_mode','legacy')=='edges' for v in values) else 'legacy'


def modern_tone(value):
    import tone_mapping
    p=params(value)
    return p.scene_hdr or p.meter_mode!='legacy' or tone_mapping.active(p)

def recipe(value,source=None,index=None):
    return {'format':'film-invert-recipe','version':6 if modern_tone(value) else 5 if params(value).perspective is not None else (4 if input_geometry(engine.STATE.get('native_input',{})) else (3 if params(value).local_edits else (2 if params(value).working_space=='prophoto' else 1))),'application':VERSION,
            'source':source or engine.STATE['path'],
            'frame':index if index is not None else engine.STATE['frame_index']+1,
            'params':params(value).__dict__,
            'input_mode':engine.STATE.get('native_input',{}).get('mode','auto'),
            'input_color':engine.STATE.get('native_input',{}).get('color','legacy'),
            'working_space':engine.STATE.get('native_input',{}).get('working_space','srgb'),
            'input_color_report':clone(engine.STATE['info'].get('input_color')),
            'source_region':source_region(None if index is None else index-1)}

def recipe_params(data):
    if not isinstance(data,dict) or data.get('format')!='film-invert-recipe' or data.get('version') not in (1,2,3,4,5,6):
        raise ValueError('不支持的配方格式')
    if (data['version'] in (4,5) or data['version']==6 and (data.get('source_region') or {}).get('angle')) and data.get('source_region')!=source_region():raise ValueError('配方的输入片框校直不同，请先打开匹配工程')
    if not isinstance(data.get('params'),dict):raise ValueError('配方参数无效')
    if data['version']<3 and data['params'].get('local_edits'):raise ValueError('局部编辑需要配方版本 3')
    if data['version']<5 and data['params'].get('perspective') is not None:raise ValueError('透视校正需要配方版本 5')
    if data['version']<6 and modern_tone(data['params']):raise ValueError('新影调参数需要配方版本 6')
    return params(data['params']).__dict__


def write_json(path,data):
    dest = io_path(path); dest.parent.mkdir(parents=True,exist_ok=True)
    temp = temporary_path(dest)
    try:
        with temp.open('x',encoding='utf-8') as stream:
            stream.write(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False))
            stream.flush();os.fsync(stream.fileno())
        temp.replace(dest)
    finally:
        temp.unlink(missing_ok=True)

def layout_identity():
    layout=engine.STATE['strip_layout']
    if layout is None:return None
    result={'boxes':[list(box) for box in strip.frame_boxes(layout)],'rotation':layout.rotation}
    if layout.angles and any(layout.angles):result['angles']=list(layout.angles)
    return result

def project_snapshot(value,options=None):
    with engine.LOCK:
        if not engine.STATE['path']:raise ValueError('请先打开图像')
        count=engine.STATE['strip_layout'].frame_count if engine.STATE['strip_layout'] else 1
        index=engine.STATE['frame_index'];saved=clone(engine.STATE['frame_params'])
        saved[index]=params(value).__dict__
        input_spec=clone(engine.STATE.get('native_input',{'mode':'auto','manual':None}))
        version=6 if input_geometry(input_spec) else (5 if any(v.get('local_edits') for v in [*saved.values(),engine.STATE.get('roll_params') or {}]) else (4 if 'working_space' in input_spec else (3 if 'color' in input_spec else (2 if input_spec['mode']!='auto' or input_spec['manual'] else 1))))
        if any(v.get('perspective') is not None for v in [*saved.values(),engine.STATE.get('roll_params') or {}]):version=7
        if any(modern_tone(v) for v in [*saved.values(),engine.STATE.get('roll_params') or {}]):version=8
        return {'format':'film-invert-project','version':version,'application':VERSION,
                'input':input_spec,
                'source':clone(engine.STATE['native_source']),
                'frame_count':count,'current_frame':index,
                'layout':layout_identity(),
                'frames':{str(k):params(v).__dict__ for k,v in saved.items()},
                'roll_params':clone(engine.STATE['roll_params']),
                'options':{'keep_params':bool((options or {}).get('keep_params',True)),
                           'auto_meter':bool((options or {}).get('auto_meter',True))}}

def save_project(path,value,options=None):
    data=project_snapshot(value,options)
    try:data['source']['relative_path']=os.path.relpath(data['source']['path'],Path(path).resolve().parent)
    except ValueError:pass  # Different Windows volumes.
    write_json(path,data)
    return str(Path(path).resolve())

def project_signature(data):
    logical={key:data.get(key) for key in ('frame_count','current_frame','layout','frames','roll_params','options')}
    input_spec=data.get('input',{'mode':'auto','manual':None})
    if input_spec!={'mode':'auto','manual':None}:logical['input']=input_spec
    logical['source_sha256']=data['source']['sha256']
    return hashlib.sha256(json.dumps(logical,sort_keys=True,allow_nan=False).encode('utf-8')).hexdigest()

def read_project(path):
    path=io_path(path)
    if path.stat().st_size>32*1024*1024:raise ValueError('工程文件超过 32 MB')
    data=json.loads(path.read_text('utf-8'))
    if not isinstance(data,dict) or data.get('format')!='film-invert-project' or data.get('version') not in (1,2,3,4,5,6,7,8):
        raise ValueError('不支持的工程格式或版本')
    data['input']=normalize_input(data.get('input',{'mode':'auto','manual':None}))
    if input_geometry(data['input']) and data['version']<6:raise ValueError('输入片框校直需要工程版本 6')
    if data['version']<3 and 'color' in data['input']:raise ValueError('输入色彩设置需要工程版本 3')
    if data['version']<4 and 'working_space' in data['input']:raise ValueError('宽色域工作空间需要工程版本 4')
    if data['version']==1 and data['input']!={'mode':'auto','manual':None}:
        raise ValueError('手动切帧或指定输入模式需要工程版本 2')
    source=data.get('source');count=data.get('frame_count');current=data.get('current_frame')
    if not isinstance(source,dict) or not isinstance(source.get('path'),str) or not isinstance(source.get('size'),int):
        raise ValueError('工程缺少原图身份信息')
    digest=source.get('sha256')
    if not isinstance(digest,str) or len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):
        raise ValueError('工程原图校验值无效')
    if type(count)!=int or not 1<=count<=5000 or type(current)!=int or not 0<=current<count:
        raise ValueError('工程帧数或当前帧号无效')
    frames=data.get('frames')
    if not isinstance(frames,dict) or str(current) not in frames or len(frames)>count:
        raise ValueError('工程缺少当前帧参数')
    if data['version']<5 and any(v.get('local_edits') for v in list(frames.values())+[data.get('roll_params') or {}] if isinstance(v,dict)):
        raise ValueError('局部编辑需要工程版本 5')
    if data['version']<7 and any(v.get('perspective') is not None for v in list(frames.values())+[data.get('roll_params') or {}] if isinstance(v,dict)):
        raise ValueError('透视校正需要工程版本 7')
    if data['version']<8 and any(modern_tone(v) for v in [*frames.values(),data.get('roll_params') or {}]):raise ValueError('新影调参数需要工程版本 8')
    for key,value in frames.items():
        if not isinstance(key,str) or not key.isdecimal() or str(int(key))!=key or not 0<=int(key)<count or not isinstance(value,dict):
            raise ValueError('工程帧参数无效')
        frames[key]=params(value).__dict__
        if frames[key]['working_space']!=data['input'].get('working_space','srgb'):raise ValueError('工程帧参数与输入工作空间不一致')
    if data.get('roll_params') is not None:data['roll_params']=params(data['roll_params']).__dict__
    if data.get('roll_params') and data['roll_params']['working_space']!=data['input'].get('working_space','srgb'):raise ValueError('整卷模板与工作空间不一致')
    options=data.get('options',{})
    if not isinstance(options,dict):raise ValueError('工程选项无效')
    data['options']={'keep_params':bool(options.get('keep_params',True)),
                     'auto_meter':bool(options.get('auto_meter',True))}
    return data

def project_source(data,path,replacement=None):
    if replacement:return Path(replacement).resolve()
    source=data['source'];candidates=[]
    if isinstance(source.get('relative_path'),str):candidates.append(Path(path).resolve().parent/source['relative_path'])
    candidates.extend([Path(source['path']),Path(path).resolve().parent/Path(source['path']).name])
    for candidate in candidates:
        if io_path(candidate).is_file():return candidate.resolve()
    raise FileNotFoundError('工程原图已移动，请重新定位：'+source['path'])

def open_project(path,replacement=None):
    data=read_project(path);source=project_source(data,path,replacement)
    import viewport_render
    viewport_render.clear_context()
    with preserve_document_on_error():
        identity=source_identity(source)
        if any(identity[key]!=data['source'][key] for key in ('size','sha256')):
            raise ValueError('所选原图与工程记录不一致，已保留当前编辑；请定位原始文件')
        spec=data['input']
        result=open_image(source,identity,include_thumbnails=False,input_mode=spec['mode'],manual=spec['manual'],input_color=spec.get('color','legacy'),working_space=spec.get('working_space','srgb'))
        _restore_legacy_detection(data)
        result['info']=clone(engine.STATE['info'])
        count=result['info']['strip']['frames'] if result['info']['strip'] else 1
        if count!=data['frame_count'] or layout_identity()!=data.get('layout'):
            raise ValueError('胶片布局与工程记录不一致，已保留当前编辑')
        result.update(_restore_edits(data))
        result.update(
                      options=data['options'],project_path=str(Path(path).resolve()),
                      saved_signature=project_signature(data))
        return result

def export_image(destination,value,array=None,source=None,index=None):
    p = engine.prepare_params(params(value)) if array is None else params(value)
    lin = engine.STATE['full'] if array is None else array
    if lin is None:
        raise ValueError('请先打开图像')
    dest = Path(destination)
    io_path(dest.parent).mkdir(parents=True,exist_ok=True)
    temp = temporary_path(dest)
    try:
        diagnostics=engine.export_pixels(str(temp),lin,p)
        temp.replace(io_path(dest))
        record=recipe(p.__dict__,source,index);record['output_diagnostics']=diagnostics
        write_json(str(dest)+'.json',record)
    finally:
        temp.unlink(missing_ok=True)
    return str(dest)

def export_roll(folder,value,meter_each,cancel,progress,export_options=None):
    img,layout,source = engine.STATE['strip_img'],engine.STATE['strip_layout'],engine.STATE['path']
    if img is None or layout is None:
        raise ValueError('请先打开整条胶片')
    saved = clone(engine.STATE['frame_params'])
    saved[engine.STATE['frame_index']] = clone(value)
    roll = clone(engine.STATE['roll_params'])
    stem = Path(source).stem
    from export_policy import names
    filenames=names(source,list(range(layout.frame_count)),export_options,lambda i:f'{stem}-f{i+1:02d}-positive.tif')
    dest = Path(folder)/(stem+'-roll-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:4])
    io_path(dest).mkdir(parents=True,exist_ok=False)
    manifest = []
    for i in range(layout.frame_count):
        if cancel.is_set():
            break
        si,_,arr = strip.extract_one(img,layout,i)
        if i in saved:
            p = params(saved[i])
        else:
            p = params(roll or {'meter_mode':engine.STATE.get('default_meter_mode','legacy')})
            p.base,p.anchor = layout.bases[si],None
            if meter_each or roll is None:
                _,p.span = core.auto_anchor(arr,p.base,p.meter_mode)
        p = engine.prepare_params(p,img,layout,i)
        if i not in saved and p.flat_mode == 'strip':
            p.base = engine.strip_field(img,layout,i)[1]
            if meter_each:
                _,p.span = core.auto_anchor(engine.corrected_input(arr,p),p.base,p.meter_mode)
        p.output_space = value.get('output_space','srgb')
        path = dest/filenames[i]
        export_image(path,p.__dict__,arr,source,i+1)
        manifest.append({'frame':i+1,'file':path.name,'params':p.__dict__,'source_region':source_region(i)})
        write_json(dest/'manifest.json',manifest)
        progress(i+1,layout.frame_count,str(path))
    cancelled = cancel.is_set() and len(manifest) < layout.frame_count
    write_json(dest/'task.json',{'cancelled':cancelled,'completed':len(manifest),'total':layout.frame_count})
    return {'folder':str(dest),'completed':len(manifest),'total':layout.frame_count,'cancelled':cancelled}


def local_preview(value):
    p=engine.prepare_params(params(value));p.local_edits=[];p.flip=False
    p.rotation,p.straighten,p.crop=0,0.,geometry.FULL.copy()
    p.perspective=None
    return {'working_pixels':core.process(engine.downscale(engine.STATE['preview'],1000),p,p.working_space),
            'working_space':p.working_space,'source_shape':engine.STATE['full'].shape[:2]}
