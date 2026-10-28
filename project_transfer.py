"""Validated current-roll transfer archive including its original; no live library replacement."""
from pathlib import Path,PurePosixPath
import copy,hashlib,json,os,re,shutil,tempfile,uuid,zipfile
from file_io import io_path,temporary_path
MAX_PROJECT=32*1024*1024
MAX_SOURCE=64*1024**3

def checked(cancel):
    if cancel is not None and cancel.is_set():raise ValueError('已取消迁移操作，未发布迁移结果')

def digest_file(stream,target=None,cancel=None):
    digest=hashlib.sha256();count=0
    while True:
        checked(cancel);data=stream.read(1024*1024)
        if not data:break
        count+=len(data);digest.update(data)
        if target is not None:target.write(data)
    return digest.hexdigest(),count

def metadata(value=None):
    value=value or {};rating=value.get('rating',0);flag=value.get('flag','none');roll=value.get('roll','')
    if type(rating)!=int or not 0<=rating<=5 or flag not in ('none','pick','reject') or not isinstance(roll,str) or len(roll)>200:raise ValueError('迁移包照片标签无效')
    return dict(rating=rating,flag=flag,roll=roll)

def pack(snapshot,destination,labels=None,cancel=None):
    import desktop_model as model
    snapshot=copy.deepcopy(snapshot);logical=Path(destination).resolve();destination=io_path(logical);source=io_path(snapshot['source']['path'])
    if destination.exists():raise ValueError('迁移包已存在，请选择新文件名')
    if not source.is_file() or source.stat().st_size!=snapshot['source']['size']:raise ValueError('原片缺失或大小改变')
    before=source.stat()
    suffix=source.suffix.lower()
    if not re.fullmatch(r'\.[a-z0-9]{1,10}',suffix):raise ValueError('原片扩展名无效')
    member='originals/source'+suffix
    snapshot['source']['path']=member;snapshot['source']['relative_path']=member
    # Do not transport machine-specific UI/queue bookkeeping.
    for key in ('project_file','saved_project_signature','_queue_engine','_export'):snapshot.pop(key,None)
    payload=json.dumps(snapshot,ensure_ascii=False,allow_nan=False).encode('utf-8')
    if len(payload)>MAX_PROJECT or not 0<source.stat().st_size<=MAX_SOURCE:raise ValueError('迁移内容超过大小限制')
    destination.parent.mkdir(parents=True,exist_ok=True);stage=temporary_path(destination)
    try:
        with zipfile.ZipFile(stage,'w',zipfile.ZIP_DEFLATED,allowZip64=True) as z:
            with source.open('rb') as raw,z.open(member,'w',force_zip64=True) as target:digest,size=digest_file(raw,target,cancel)
            if digest!=snapshot['source']['sha256'] or size!=snapshot['source']['size'] or (source.stat().st_size,source.stat().st_mtime_ns)!=(before.st_size,before.st_mtime_ns):raise ValueError('原片内容已改变，未发布迁移包')
            z.writestr('project.filmproj',payload)
            manifest={'format':'film-invert-transfer','version':1,'original':member,'files':{member:{'bytes':size,'sha256':digest},'project.filmproj':{'bytes':len(payload),'sha256':hashlib.sha256(payload).hexdigest()}},'metadata':metadata(labels)}
            z.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False))
        checked(cancel)
        if os.name=='nt':os.rename(stage,destination)
        else:os.link(stage,destination);stage.unlink()
    finally:stage.unlink(missing_ok=True)
    return str(logical)

def unpack(archive,parent,cancel=None):
    import desktop_model as model
    logical=Path(parent).resolve();parent=io_path(logical);parent.mkdir(parents=True,exist_ok=True)
    final=parent/('胶卷迁移-'+uuid.uuid4().hex[:12]);stage=Path(tempfile.mkdtemp(dir=parent,prefix='.transfer-'))
    try:
        with zipfile.ZipFile(io_path(archive)) as z:
            if len(z.infolist())!=3 or len(set(z.namelist()))!=3:raise ValueError('迁移包文件数量无效')
            if z.getinfo('manifest.json').file_size>65536:raise ValueError('迁移清单过大')
            manifest=json.loads(z.read('manifest.json'))
            if manifest.get('format')!='film-invert-transfer' or manifest.get('version')!=1:raise ValueError('不支持的迁移包格式')
            member=manifest.get('original','')
            if not re.fullmatch(r'originals/source\.[a-z0-9]{1,10}',member) or set(z.namelist())!={'manifest.json','project.filmproj',member}:raise ValueError('迁移包路径或文件列表无效')
            labels=metadata(manifest.get('metadata'));files=manifest.get('files',{})
            if set(files)!={member,'project.filmproj'}:raise ValueError('迁移包清单不完整')
            for name,limit in ((member,MAX_SOURCE),('project.filmproj',MAX_PROJECT)):
                item=z.getinfo(name);record=files[name]
                if not 0<item.file_size<=limit or item.file_size!=record.get('bytes') or (item.external_attr>>16)&0o170000==0o120000:raise ValueError('迁移文件大小或类型无效')
                target=stage/Path(*PurePosixPath(name).parts);target.parent.mkdir(parents=True,exist_ok=True)
                with z.open(name) as raw,target.open('wb') as output:digest,size=digest_file(raw,output,cancel)
                if digest!=record.get('sha256') or size!=record['bytes']:raise ValueError('迁移文件 SHA256 校验失败')
            project=model.read_project(stage/'project.filmproj')
            if project['source'].get('relative_path')!=member or project['source']['path']!=member or any(project['source'][k]!=files[member][v] for k,v in (('size','bytes'),('sha256','sha256'))):raise ValueError('工程与原片清单不一致')
            project['source']['path']=str(logical/final.name/Path(*PurePosixPath(member).parts))
            model.write_json(stage/'project.filmproj',project)
        checked(cancel);stage.rename(final)
    except Exception:
        # Only remove this function's newly-created private staging directory.
        if stage.resolve().parent!=parent or not stage.name.startswith('.transfer-'):raise
        shutil.rmtree(stage);raise
    return {'project':str(logical/final.name/'project.filmproj'),'folder':str(logical/final.name),'metadata':labels}
