"""Consistent SQLite backup and validated, reversible library restoration."""
from pathlib import Path
import hashlib
import json
import os
import sqlite3
import tempfile
import time
import uuid
import zipfile
from contextlib import closing
from studio_store import queue_lock
from file_io import io_path,temporary_path

LIMIT=512*1024*1024

def backup(store,destination):
    logical=Path(destination).resolve();destination=io_path(logical)
    destination.parent.mkdir(parents=True,exist_ok=True)
    if destination==io_path(store.path.resolve()):raise ValueError('不能覆盖正在使用的照片库')
    with tempfile.TemporaryDirectory(dir=io_path(store.folder),prefix='backup-') as temporary:
        path=Path(temporary)/'library.sqlite3'
        with store.connection() as source,closing(sqlite3.connect(path)) as target:source.backup(target)
        if path.stat().st_size>LIMIT:raise ValueError('照片库备份超过 512 MB，请先整理照片库')
        manifest={'format':'film-invert-studio-backup','version':1,'created':time.time(),
                  'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'originals_included':False}
        stage=temporary_path(destination)
        try:
            with zipfile.ZipFile(stage,'w',zipfile.ZIP_DEFLATED) as archive:
                archive.write(path,'library.sqlite3');archive.writestr('manifest.json',json.dumps(manifest))
            os.replace(stage,destination)
        finally:stage.unlink(missing_ok=True)
    return str(logical)

def restore(store,archive_path):
    from batch_store import BatchStore,validate_payload,settings
    BatchStore(store)
    with queue_lock(store.folder),queue_lock(store.folder/'batch-work'),tempfile.TemporaryDirectory(dir=io_path(store.folder),prefix='restore-') as temporary:
        path=Path(temporary)/'library.sqlite3'
        with zipfile.ZipFile(io_path(archive_path)) as archive:
            if sorted(archive.namelist())!=['library.sqlite3','manifest.json']:raise ValueError('备份内容无效')
            if archive.getinfo('library.sqlite3').file_size>LIMIT or archive.getinfo('manifest.json').file_size>4096:
                raise ValueError('备份超过大小限制')
            manifest=json.loads(archive.read('manifest.json'))
            if manifest.get('format')!='film-invert-studio-backup' or manifest.get('version')!=1:
                raise ValueError('不支持的备份格式')
            content=archive.read('library.sqlite3')
            if hashlib.sha256(content).hexdigest()!=manifest.get('sha256'):raise ValueError('备份校验失败')
            path.write_bytes(content)
        with closing(sqlite3.connect(path)) as source:
            source.execute('PRAGMA trusted_schema=OFF')
            if source.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('备份数据库损坏')
            if source.execute('PRAGMA user_version').fetchone()[0]!=1:raise ValueError('不支持的照片库版本')
            tables={row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            workflow={'workflow_batches','workflow_templates','workflow_history'}
            if not {'assets','jobs'}<=tables or tables-{'assets','jobs'} not in (set(),workflow):
                raise ValueError('备份含有未知数据表')
            if source.execute("SELECT count(*) FROM sqlite_master WHERE type IN ('trigger','view')").fetchone()[0]:
                raise ValueError('备份含有不支持的数据库对象')
            # Require the known table structure before any changes to the live library.
            for table in sorted(tables):
                with store.connection() as current:
                    expected=[tuple(row) for row in current.execute('PRAGMA table_info('+table+')')]
                actual=source.execute('PRAGMA table_info('+table+')').fetchall()
                if actual!=expected:raise ValueError('备份数据结构不兼容')
            source.row_factory=sqlite3.Row
            import desktop_model as model
            for table in ('assets','jobs'):
                for row in source.execute('SELECT * FROM '+table):
                    if len(row['id'])!=32 or any(c not in '0123456789abcdef' for c in row['id']):
                        raise ValueError('备份记录编号无效')
                    if row['snapshot']:
                        candidate=Path(temporary)/'validate.filmproj';candidate.write_text(row['snapshot'],encoding='utf-8')
                        model.read_project(candidate)
                    if table=='assets' and (row['flag'] not in ('none','pick','reject') or not 0<=row['rating']<=5):
                        raise ValueError('备份评级无效')
                    if table=='jobs':
                        if row['status'] not in ('queued','running','paused','failed','completed'):raise ValueError('备份任务状态无效')
                        if not isinstance(json.loads(row['indices']),list) or not isinstance(json.loads(row['outputs']),dict):
                            raise ValueError('备份队列数据无效')
            source.execute("UPDATE jobs SET status='paused',cancel=0,worker_pid=NULL,error='从备份恢复，请核对原片与输出目录后继续' WHERE status!='completed'")
            if workflow<=tables:
                for row in source.execute('SELECT id,payload FROM workflow_batches').fetchall():
                    batch=validate_payload(json.loads(row['payload']));batch['status']='paused'
                    for item in batch['items']:item['valid']=False;item['reviewed']=False
                    source.execute('UPDATE workflow_batches SET payload=? WHERE id=?',(json.dumps(batch,ensure_ascii=False,allow_nan=False),row['id']))
                for row in source.execute('SELECT payload FROM workflow_templates'):
                    template=json.loads(row['payload'])
                    if template.get('format')!='film-invert-batch-template' or template.get('version')!=1:raise ValueError('批次模板无效')
                    settings(template['config'])
                for row in source.execute('SELECT before_state,after_state FROM workflow_history'):
                    for key in ('before_state','after_state'):
                        value=json.loads(row[key])
                        if not isinstance(value,dict) or not isinstance(value.get('item'),dict):raise ValueError('批次撤销记录无效')
            source.commit()
            recovery=store.folder/'backups'/('before-restore-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]+'.filmbackup')
            backup(store,recovery)
            with store.connection() as destination:source.backup(destination)
    BatchStore(store)
    return str(recovery)
