"""Serial, resumable batch preparation/style/report operations, run in a child."""
from pathlib import Path
import copy,gc,hashlib,json,os,time,uuid
import numpy as np
from PIL import Image,ImageDraw,ImageCms
from batch_store import BatchStore,FIELDS,clone,snapshot_key
from studio_store import queue_lock
from file_io import io_path

def release_image():
    import desktop_model as model,viewport_render
    model.DOCUMENTS.clear()
    model.NAV_THUMBS.clear()
    viewport_render.clear_context()
    with model.engine.LOCK:
        model.engine.STATE.update(path=None,full=None,preview=None,strip_img=None,strip_layout=None,native_loaded=None,
                                  native_source=None,frame_params={},roll_params=None,info={})
        model.engine.THUMB_CACHE.clear();model.engine.FLAT_CACHE.clear()
    model.PREVIEWS.entries.clear();model.PREVIEWS.bytes=0;gc.collect()

def checked_identity(studio,item,overwrite=False):
    import desktop_model as model
    asset=studio.get_asset(item['asset']);identity=model.source_identity(asset['path'])
    saved=item['snapshot'] or asset['snapshot']
    if saved and not overwrite and any(identity[k]!=saved['source'][k] for k in ('size','sha256')):
        raise ValueError('原片与保存编辑的身份不符；已保留旧编辑。请核对或明确重新准备：'+asset['name'])
    return identity

def fresh_snapshot(studio,item,config,overwrite):
    import desktop_model as model
    asset=studio.get_asset(item['asset']);checked_identity(studio,item,overwrite)
    saved=item['snapshot'] or asset['snapshot']
    if config['method']=='existing' and not saved:raise ValueError('此照片没有已有编辑')
    if saved and not overwrite:
        model.open_project(studio.materialize(saved,'batch-'+item['asset']));return clone(saved)
    opened=model.open_image(asset['path'],include_thumbnails=False,input_mode=item['input_mode'] or config['input_mode'],
                            input_color=config['input_color'],working_space=config['working_space'])
    p=opened['params'];count=(opened['info'].get('strip') or {}).get('frames',1)
    for index in range(1,count):p=model.change_frame(index,p,False,True)['params']
    snapshot=model.project_snapshot(p)
    snapshot['current_frame']=0
    return snapshot

def reference_params(data,item):
    group=data['groups'].get(item['group'],{});reference=group.get('reference')
    if reference:
        candidate=next((x for x in data['items'] if x['asset']==reference and x['group']==item['group']),None)
        if not candidate or not candidate['snapshot'] or not candidate['valid'] or candidate['error']:raise ValueError('本组参考图尚未准备或校验')
        snapshot=candidate['snapshot'];return snapshot['frames'][str(snapshot['current_frame'])],group
    if group.get('values'):return group['values'],group
    raise ValueError('请先为本组选择参考图：'+item['group'])

def sync_snapshot(snapshot,origin,reference,fields,sync_mode,delta,offset,shared_base=False):
    """Only whitelisted fields cross images; geometry/base/light/local stay local."""
    import desktop_model as model,core
    if reference['working_space']!=snapshot['input'].get('working_space','srgb'):
        raise ValueError('参考图工作空间不同，请按工作空间分组或使用匹配参考图')
    if shared_base and (not reference.get('base') or core.base_clipped_channels(reference['base'])):
        raise ValueError('共享片基必须来自未剪裁的有效实测参考片基')
    out=clone(snapshot)
    for key,target in out['frames'].items():
        for group in fields:
            for field in FIELDS[group]:target[field]=clone(reference[field])
        if 'exposure' in fields:
            target['exposure']=(origin['frames'].get(key,target)['exposure']+delta if sync_mode=='relative' else reference['exposure'])+offset
        elif offset:target['exposure']=origin['frames'].get(key,target)['exposure']+offset
        if shared_base:
            from studio_worker import frame_parameters
            array,_=frame_parameters(snapshot,int(key));target['base']=clone(reference['base']);target['anchor']=None
            _,target['span']=core.auto_anchor(array,target['base'])
        out['frames'][key]=model.params(target).__dict__
    if out.get('roll_params'):
        # All prepared frames are materialized; roll defaults must not retain a
        # different future style if the project is later opened and edited.
        roll=out['roll_params']
        for group in fields:
            for field in FIELDS[group]:roll[field]=clone(reference[field])
        out['roll_params']=model.params(roll).__dict__
    return out

def thumbnail(studio,batch,item,snapshot,prefix):
    import desktop_model as model
    index=snapshot['current_frame'];value=snapshot['frames'][str(index)]
    # The loaded source may be on the last preparation frame: set the intended
    # frame explicitly before rendering the representative batch thumbnail.
    if model.engine.STATE['strip_layout'] and model.engine.STATE['frame_index']!=index:
        model.change_frame(index,value,False,False)
    preview=model.preview(value)['pixels'];image=Image.fromarray(preview);image.thumbnail((480,320),Image.Resampling.LANCZOS)
    digest=hashlib.sha256(image.tobytes()).hexdigest()[:16]
    folder=io_path(studio.folder/'batch-previews'/batch);folder.mkdir(parents=True,exist_ok=True)
    path=folder/(prefix+'-'+item['asset']+'-'+digest+'.jpg')
    if not path.exists():image.save(path,quality=90)
    return str(path)

def report(studio,store,identifier):
    """Read completed outputs for an sRGB contact sheet; never re-export a TIFF."""
    import desktop_model as model,colorio,io
    data=store.get(identifier);root=Path(data.get('export_root') or data['config']['output'] or studio.folder/'batch-reports')
    folder=io_path(root/('report-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]));folder.mkdir(parents=True,exist_ok=False)
    entries=[];tiles=[];failures=[]
    for item in data['items']:
        if item['error']:failures.append({'name':item['name'],'stage':item['stage'],'error':item['error']})
        job=studio.get_job(item['job']) if item.get('job') else None
        if job and job['error']:failures.append({'name':item['name'],'stage':'export','error':job['error']})
        outputs=[]
        if job:
            for key,record in job['outputs'].items():
                if record['state']!='done':continue
                path=io_path(Path(job['destination'])/record['tiff']['name'])
                try:
                    from studio_store import sha256
                    if sha256(path)!=record['tiff']['sha256']:raise ValueError('输出校验不符')
                    with Image.open(path) as source:
                        profile=source.info.get('icc_profile');source.thumbnail((350,230),Image.Resampling.LANCZOS);image=source.convert('RGB')
                    if profile:image=ImageCms.profileToProfile(image,ImageCms.ImageCmsProfile(io.BytesIO(profile)),ImageCms.createProfile('sRGB'),outputMode='RGB')
                    tiles.append((item['name']+' #'+str(int(key)+1),image))
                    outputs.append({'frame':int(key)+1,'path':str(path),'params':record['params'],'sha256':record['tiff']['sha256']})
                except Exception as error:failures.append({'name':item['name'],'stage':'contact','error':str(error)})
        entries.append({'name':item['name'],'asset':item['asset'],'group':item['group'],'selected':item['selected'],'skipped':item['skip'],
                        'stage':item['stage'],'reviewed':item['reviewed'],'job':item.get('job'),'job_status':job['status'] if job else None,
                        'snapshot':item['snapshot'],'outputs':outputs})
    pages=[]
    for start in range(0,len(tiles),16):
        page=Image.new('RGB',(1500,1200),'#191919');draw=ImageDraw.Draw(page)
        for n,(name,image) in enumerate(tiles[start:start+16]):
            x=20+(n%4)*370;y=20+(n//4)*290;page.paste(image,(x+(350-image.width)//2,y))
            # Keep CJK names in JSON and use a font available on Windows when possible.
            from PIL import ImageFont
            try:font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',16)
            except OSError:font=ImageFont.load_default()
            draw.text((x,y+240),name[:36],fill='white',font=font)
        path=folder/('contact-'+str(len(pages)+1).zfill(3)+'.png');page.save(path,icc_profile=colorio.icc_profile('srgb'));pages.append(str(path))
    result={'format':'film-invert-batch-report','version':1,'batch':identifier,'name':data['name'],'created':time.time(),
            'files':len(entries),'completed_frames':len(tiles),'contact_pages':pages,'items':entries,'failures':failures}
    model.write_json(folder/'batch-summary.json',result)
    with studio.connection() as db:
        db.execute('BEGIN IMMEDIATE');current=store._read(db,identifier)
        current['report']={'path':str(folder),'fingerprint':export_fingerprint(studio,current)};current.pop('report_error',None);store._save(db,current)
    return str(folder)

def export_fingerprint(studio,data):
    parts=[]
    for item in data['items']:
        if item.get('job'):
            job=studio.get_job(item['job']);parts.append([job['id'],job['status'],job['done_count'],job['error']])
    return hashlib.sha256(json.dumps(parts,sort_keys=True).encode()).hexdigest()

def execute(studio,identifier,token,progress=lambda *a:None):
    import desktop_model as model
    store=BatchStore(studio)
    with queue_lock(studio.folder/'batch-work'):
        data=store.get(identifier);run=data['run']
        if not run or run['id']!=token or data['status']!='running':raise ValueError('批次运行记录已改变')
        store.run_update(identifier,pid=os.getpid());config=run['config'];operation=run['operation']
        try:
            if operation=='report':report(studio,store,identifier);store.finish(identifier,'idle');return
            operations=['check','prepare','style'] if operation=='pipeline' else [operation]
            if operation=='pipeline':operations=operations[:operations.index(config['stop_after'])+1]
            ids=[i['asset'] for i in data['items'] if not i['skip'] and i['selected'] and (run['ids'] is None or i['asset'] in run['ids'])]
            label=('批量重新准备' if run['overwrite'] else '批次同步')+' · '+token
            for stage in operations:
                # Freeze reference values for an entire stage, even when the
                # reference itself is among the files being changed.
                stage_data=store.get(identifier)
                for number,asset_id in enumerate(ids):
                    if store.cancelled(identifier):store.finish(identifier,'paused');return
                    item=clone(next(x for x in store.get(identifier)['items'] if x['asset']==asset_id));release_image()
                    try:
                        if stage=='check':
                            identity=checked_identity(studio,item,run['overwrite']);item.update(identity=identity,valid=True,verified_session=run['session'],error='')
                            asset=studio.get_asset(asset_id);refreshed=clone(asset['snapshot'])
                            for key in ('snapshot','origin'):
                                if item.get(key) and item[key]['source']['sha256']==identity['sha256']:item[key]['source']=clone(identity)
                            if refreshed and refreshed['source']['sha256']==identity['sha256']:refreshed['source']=clone(identity)
                            if not item['snapshot']:item['stage']='checked'
                            store.commit_item(identifier,item,refreshed,expected_key=snapshot_key(asset['snapshot']))
                        elif stage=='prepare':
                            if item.get('error') and not run['overwrite']:continue
                            existing=studio.get_asset(asset_id)['snapshot'];expected=snapshot_key(existing)
                            if not run['overwrite'] and item['snapshot'] and item['origin'] and expected==snapshot_key(item['snapshot']) and io_path(item['preview_before']).is_file():
                                progress(stage,number+1,len(ids),item['name']+'（已准备，跳过重算）');continue
                            if not run['overwrite'] and existing and snapshot_key(existing)!=snapshot_key(item['snapshot']):
                                item['snapshot']=clone(existing);item['origin']=None;item['reviewed']=False
                            snapshot=fresh_snapshot(studio,item,config,run['overwrite'])
                            item.update(snapshot=snapshot,origin=clone(snapshot) if run['overwrite'] or not item['origin'] else item['origin'],valid=True,verified_session=run['session'],error='',stage='prepared')
                            if run['overwrite']:item['reviewed']=False
                            item['preview_before']=thumbnail(studio,identifier,item,snapshot,'before');item['preview_after']=item['preview_before']
                            store.commit_item(identifier,item,snapshot,label if run['overwrite'] and existing else None,expected)
                        elif stage=='style':
                            if not item['snapshot'] or not item['valid'] or item['error']:continue
                            expected=snapshot_key(studio.get_asset(asset_id)['snapshot'])
                            if expected!=snapshot_key(item['snapshot']):raise ValueError('照片库已有其他编辑，请先独立准备以保留最新编辑')
                            if config['method']!='sync' and not item['ev']:
                                item.update(stage='styled',reviewed=False);store.commit_item(identifier,item);progress(stage,number+1,len(ids),item['name']);continue
                            checked_identity(studio,item);model.open_project(studio.materialize(item['snapshot'],'batch-'+asset_id))
                            if config['method']=='sync':
                                reference,group=reference_params(stage_data,item)
                                if group.get('share_base') and not group.get('confirmed'):raise ValueError('共享片基尚未明确确认')
                                snapshot=sync_snapshot(item['snapshot'],item['origin'] or item['snapshot'],reference,config['fields'],config['sync_mode'],config['exposure_delta'],float(item['ev']),group.get('share_base',False))
                            else:snapshot=clone(item['snapshot'])
                            item.update(snapshot=snapshot,stage='styled',error='',reviewed=False)
                            item['preview_after']=thumbnail(studio,identifier,item,snapshot,'after')
                            store.commit_item(identifier,item,snapshot,label if config['method']=='sync' else None,expected)
                        elif stage=='preview':
                            if not item['snapshot']:continue
                            checked_identity(studio,item);model.open_project(studio.materialize(item['snapshot'],'batch-'+asset_id))
                            item['preview_after']=thumbnail(studio,identifier,item,item['snapshot'],'after')
                            if not item['preview_before']:item['preview_before']=item['preview_after']
                            store.commit_item(identifier,item)
                        progress(stage,number+1,len(ids),item['name'])
                    except Exception as error:
                        item.update(error=str(error)[-4000:],failed_step=stage,stage='failed')
                        if stage=='check':item['valid']=False
                        store.commit_item(identifier,item)
                    finally:release_image()
            store.finish(identifier,'idle')
        except Exception:
            store.finish(identifier,'paused');raise
        finally:release_image()
