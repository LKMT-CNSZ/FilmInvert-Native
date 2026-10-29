"""Local library and durable export journal. Originals are never copied or edited."""
from pathlib import Path
from contextlib import contextmanager
import hashlib
import json
import os
import sqlite3
import time
import uuid
import sys
from file_io import io_path


def encoded(value):return json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':'))
class QueueBusy(RuntimeError):pass
def sha256(path):
    with io_path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def engine_signature():
    import numpy
    import PIL
    root=Path(__file__).resolve().parent
    files=('hdr_io.py','hdr_color.py','metering.py','tone_mapping.py','core.py','viewport_render.py','region_context.py','calibration_solver.py','chart_metrics.py','export_policy.py','local_edits.py','calibration.py','restoration.py','colorio.py','input_color.py','working_color.py','geometry.py','perspective.py','server.py','strip.py','loader.py','tiffio.py','tiffread.py','desktop_model.py','document_cache.py','studio_worker.py','file_io.py')
    versions={'python':list(sys.version_info[:3]),'numpy':numpy.__version__,'pillow':PIL.__version__,
              'files':{name:sha256(root/name) for name in files}}
    dependencies=root.parent/'dependencies.json'
    if dependencies.is_file():versions['dependencies']=sha256(dependencies)
    return hashlib.sha256(encoded(versions).encode('utf-8')).hexdigest()


@contextmanager
def queue_lock(folder):
    path=io_path(folder)/'queue.lock';path.parent.mkdir(parents=True,exist_ok=True)
    stream=path.open('a+b')
    if not path.stat().st_size:stream.write(b'0');stream.flush()
    stream.seek(0)
    try:
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError:
        stream.close();raise QueueBusy('另一个窗口正在使用导出队列')
    try:yield
    finally:
        stream.seek(0)
        if os.name=='nt':msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
        else:fcntl.flock(stream,fcntl.LOCK_UN)
        stream.close()


class StudioStore:
    def __init__(self,profile):
        self.folder=Path(profile)/'studio';io_path(self.folder).mkdir(parents=True,exist_ok=True)
        self.path=self.folder/'library.sqlite3'
        from preview_cache import ThumbnailCache
        self.thumbnails=ThumbnailCache(self.folder/'thumbnail-cache')
        with self.connection() as db:
            version=db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0,1):raise ValueError('照片库版本较新，请使用对应的软件版本')
            db.executescript('''
            CREATE TABLE IF NOT EXISTS assets(
              id TEXT PRIMARY KEY, source_key TEXT UNIQUE NOT NULL, path TEXT NOT NULL,
              name TEXT NOT NULL, rating INTEGER NOT NULL DEFAULT 0, flag TEXT NOT NULL DEFAULT 'none',
              roll TEXT NOT NULL DEFAULT '', input_mode TEXT NOT NULL DEFAULT 'auto', snapshot TEXT,
              updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs(
              id TEXT PRIMARY KEY, asset_id TEXT NOT NULL, name TEXT NOT NULL, snapshot TEXT NOT NULL,
              indices TEXT NOT NULL, destination TEXT NOT NULL, status TEXT NOT NULL,
              outputs TEXT NOT NULL DEFAULT '{}', cancel INTEGER NOT NULL DEFAULT 0,
              done_count INTEGER NOT NULL DEFAULT 0, total_count INTEGER NOT NULL DEFAULT 0,
              error TEXT NOT NULL DEFAULT '', worker_pid INTEGER, created REAL NOT NULL, updated REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status,created);
            PRAGMA user_version=1;''')
            columns={row['name'] for row in db.execute('PRAGMA table_info(jobs)')}
            for name in ('done_count','total_count'):
                if name not in columns:db.execute('ALTER TABLE jobs ADD COLUMN '+name+' INTEGER NOT NULL DEFAULT 0')

    @contextmanager
    def connection(self):
        db=sqlite3.connect(io_path(self.path),timeout=15);db.row_factory=sqlite3.Row
        try:
            db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA synchronous=FULL')
            with db:yield db
        finally:db.close()

    def import_paths(self,paths,input_mode='auto'):
        from loader import SUPPORTED_EXTS
        added=0;skipped=[];existing=0;identifiers=[];seen=set()
        if input_mode not in ('auto','single','strip'):raise ValueError('打开方式无效')
        with self.connection() as db:
            for value in paths:
                path=Path(value).resolve()
                if not io_path(path).is_file() or path.suffix.lower() not in SUPPORTED_EXTS:
                    skipped.append(str(path));continue
                key=os.path.normcase(str(path))
                if key in seen:existing+=1;continue
                seen.add(key)
                cursor=db.execute('INSERT OR IGNORE INTO assets(id,source_key,path,name,input_mode,updated) VALUES(?,?,?,?,?,?)',
                    (uuid.uuid4().hex,key,str(path),path.name,input_mode,time.time()))
                added+=cursor.rowcount
                existing+=1-cursor.rowcount
                identifiers.append(db.execute('SELECT id FROM assets WHERE source_key=?',(key,)).fetchone()['id'])
        return {'added':added,'skipped':skipped,'existing':existing,'ids':identifiers}

    def capture(self,snapshot):
        path=Path(snapshot['source']['path']).resolve();key=os.path.normcase(str(path));payload=encoded(snapshot)
        if len(payload.encode('utf-8'))>32*1024*1024:raise ValueError('照片库工程超过 32 MB')
        with self.connection() as db:
            db.execute('INSERT OR IGNORE INTO assets(id,source_key,path,name,updated) VALUES(?,?,?,?,?)',
                (uuid.uuid4().hex,key,str(path),path.name,time.time()))
            db.execute('UPDATE assets SET snapshot=?,input_mode=?,updated=? WHERE source_key=?',
                (payload,snapshot.get('input',{}).get('mode','auto'),time.time(),key))
            return db.execute('SELECT id FROM assets WHERE source_key=?',(key,)).fetchone()['id']

    @staticmethod
    def asset(row):
        if row is None:raise ValueError('照片库记录不存在')
        result=dict(row);result['snapshot']=json.loads(result['snapshot']) if result['snapshot'] else None
        return result
    def get_asset(self,identifier):
        with self.connection() as db:return self.asset(db.execute('SELECT * FROM assets WHERE id=?',(identifier,)).fetchone())
    def assets(self,search='',rating=0,hide_rejected=False,include_snapshot=True):
        selection='*' if include_snapshot else 'id,source_key,path,name,rating,flag,roll,input_mode,updated,NULL AS snapshot,(snapshot IS NOT NULL) AS has_edits'
        with self.connection() as db:rows=db.execute('SELECT '+selection+' FROM assets WHERE rating>=? ORDER BY updated DESC',(int(rating),)).fetchall()
        search=search.casefold().strip()
        return [self.asset(row) for row in rows if (not hide_rejected or row['flag']!='reject') and
                (not search or search in (row['name']+' '+row['path']+' '+row['roll']).casefold())]
    def metadata(self,identifier,rating,flag,roll):
        if type(rating)!=int or not 0<=rating<=5 or flag not in ('none','pick','reject'):raise ValueError('评级或标记无效')
        with self.connection() as db:db.execute('UPDATE assets SET rating=?,flag=?,roll=? WHERE id=?',(rating,flag,str(roll)[:200],identifier))

    def save_thumbnail(self,path,pixels):
        from PIL import Image
        key=os.path.normcase(str(Path(path).resolve()))
        with self.connection() as db:row=db.execute('SELECT * FROM assets WHERE source_key=?',(key,)).fetchone()
        if row is not None:
            asset=self.asset(row);self.thumbnails.save(self.thumbnails.key(asset),Image.fromarray(pixels))

    def relink(self,identifiers,folder):
        """Relink missing originals only; queue snapshots remain immutable."""
        from desktop_model import source_identity
        from loader import SUPPORTED_EXTS
        candidates={};errors=[];changed={}
        for root,dirs,files in os.walk(folder):
            for name in files:
                path=Path(root)/name
                if path.suffix.lower() in SUPPORTED_EXTS:candidates.setdefault(name.casefold(),[]).append(path)
        for identifier in identifiers:
            asset=self.get_asset(identifier);old=asset['path'];snapshot=asset['snapshot']
            if Path(old).is_file():continue
            if not snapshot:errors.append(asset['name']+'：未保存编辑，缺少原片校验值');continue
            expected=snapshot['source'];matches=[]
            for path in candidates.get(asset['name'].casefold(),[]):
                if path.stat().st_size!=expected['size']:continue
                identity=source_identity(path)
                if identity['sha256']==expected['sha256']:matches.append(identity)
            if len(matches)!=1:errors.append(asset['name']+'：未找到唯一且内容一致的原片');continue
            identity=matches[0];key=os.path.normcase(identity['path'])
            snapshot['source']=identity;snapshot['source'].pop('relative_path',None)
            with self.connection() as db:
                conflict=db.execute('SELECT id FROM assets WHERE source_key=? AND id!=?',(key,identifier)).fetchone()
                if conflict:errors.append(asset['name']+'：新位置已在照片库中');continue
                db.execute('UPDATE assets SET source_key=?,path=?,name=?,snapshot=?,updated=? WHERE id=?',
                    (key,identity['path'],Path(identity['path']).name,encoded(snapshot),time.time(),identifier))
            changed[old]=identity['path']
        return {'changed':changed,'errors':errors}

    def materialize(self,data,name):
        from desktop_model import write_json
        folder=self.folder/'snapshots';io_path(folder).mkdir(exist_ok=True)
        path=folder/(name+'.filmproj');write_json(path,data)
        return path
    def open_asset(self,identifier):
        import desktop_model as model
        row=self.get_asset(identifier);snapshot=row['snapshot']
        if snapshot is None:return model.open_image(row['path'],input_mode=row['input_mode'])
        result=model.open_project(self.materialize(snapshot,'asset-'+row['id']))
        result['project_path']=snapshot.get('project_file','')
        result['saved_signature']=snapshot.get('saved_project_signature')
        return result

    def enqueue(self,identifiers,output,whole_roll=True,export_options=None):
        output=Path(output).resolve();made=[]
        with self.connection() as db:
            for identifier in dict.fromkeys(identifiers):
                asset=self.asset(db.execute('SELECT * FROM assets WHERE id=?',(identifier,)).fetchone())
                snapshot=asset['snapshot']
                if snapshot is None:raise ValueError('请先打开并修片：'+asset['name'])
                if export_options is not None:
                    from export_policy import options
                    snapshot['_export']=options(export_options.get('template',''),asset['roll'],export_options.get('date',''))
                snapshot['_queue_engine']=engine_signature()
                count=snapshot['frame_count'];current=snapshot['current_frame']
                indices=list(range(count)) if whole_roll else [current]
                from export_policy import names
                names(snapshot['source']['path'],indices,snapshot.get('_export'))
                stem=''.join('_' if c in '<>:"/\\|?*' or ord(c)<32 else c for c in Path(asset['name']).stem)[:60].rstrip(' .')
                job=uuid.uuid4().hex;destination=output/('film-'+stem+'-'+job)
                now=time.time()
                db.execute('INSERT INTO jobs(id,asset_id,name,snapshot,indices,destination,status,total_count,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (job,identifier,asset['name'],encoded(snapshot),encoded(indices),str(destination),'queued',len(indices),now,now));made.append(job)
        return made
    @staticmethod
    def job(row):
        if row is None:raise ValueError('队列任务不存在')
        result=dict(row)
        for key in ('snapshot','indices','outputs'):result[key]=json.loads(result[key])
        return result
    def get_job(self,identifier):
        with self.connection() as db:return self.job(db.execute('SELECT * FROM jobs WHERE id=?',(identifier,)).fetchone())
    def jobs(self):
        with self.connection() as db:return [self.job(row) for row in db.execute('SELECT * FROM jobs ORDER BY created DESC')]
    def job_summaries(self):
        with self.connection() as db:return [dict(row) for row in db.execute('SELECT id,name,status,done_count,total_count,created,destination,error FROM jobs ORDER BY created DESC')]
    def next_job(self):
        with self.connection() as db:
            row=db.execute("SELECT id FROM jobs WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
            return row['id'] if row else None
    def claim(self,identifier):
        with self.connection() as db:
            cursor=db.execute("UPDATE jobs SET status='running',worker_pid=?,error='',updated=? WHERE id=? AND status='queued'",
                (os.getpid(),time.time(),identifier))
            if cursor.rowcount!=1:raise ValueError('任务已由其他窗口处理，或不处于待处理状态')
    def status(self,identifier,status,error=''):
        if status not in ('running','paused','failed','completed'):raise ValueError('队列状态无效')
        with self.connection() as db:db.execute('UPDATE jobs SET status=?,error=?,updated=? WHERE id=?',(status,str(error)[-4000:],time.time(),identifier))
    def request_pause(self,identifier):
        with self.connection() as db:db.execute("UPDATE jobs SET cancel=1 WHERE id=? AND status IN ('queued','running')",(identifier,))
    def cancelled(self,identifier):
        with self.connection() as db:return bool(db.execute('SELECT cancel FROM jobs WHERE id=?',(identifier,)).fetchone()[0])
    def checkpoint(self,identifier,index,output):
        with self.connection() as db:
            row=db.execute('SELECT outputs FROM jobs WHERE id=?',(identifier,)).fetchone()
            outputs=json.loads(row['outputs']);outputs[str(index)]=output
            done=sum(item.get('state')=='done' for item in outputs.values())
            db.execute('UPDATE jobs SET outputs=?,done_count=?,updated=? WHERE id=?',(encoded(outputs),done,time.time(),identifier))
    def retry(self,identifiers):
        with self.connection() as db:
            for identifier in identifiers:
                db.execute("UPDATE jobs SET status='queued',cancel=0,error='',worker_pid=NULL,updated=? WHERE id=? AND status IN ('paused','failed')",(time.time(),identifier))
    def recover(self):
        try:
            with queue_lock(self.folder):
                with self.connection() as db:db.execute("UPDATE jobs SET status='paused',cancel=0,worker_pid=NULL,error='上次进程未正常结束，可继续任务',updated=? WHERE status='running'",(time.time(),))
            return True
        except QueueBusy:return False

