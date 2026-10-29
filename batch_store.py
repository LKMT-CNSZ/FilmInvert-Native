"""Versioned batch state in the existing durable library, separate from queue snapshots."""
from pathlib import Path
import copy,hashlib,json,time,uuid
from studio_store import encoded,engine_signature,queue_lock,QueueBusy
from file_io import io_path

FIELDS={'style':('mode','film','slope','d_min','saturation','crosstalk'),
        'wb':('temp','tint','wb_gain'),'exposure':('exposure',),
        'tone':('black','white','curve','hsl','tone_shadows','tone_highlights','tone_whites','tone_blacks','tone_rolloff','local_tone','local_radius','local_detail'),'output':('output_space',),
        'calibration':('calibration','calibration_strength')}
DEFAULT={'input_mode':'single','input_color':'legacy','working_space':'srgb','method':'auto',
         'fields':['style','output'],'sync_mode':'absolute','exposure_delta':0.,
         'group_rule':'manual','stop_after':'style','require_review':True,'whole_roll':True,
         'template':'{source}-f{frame:04d}-positive','group_directories':True,'output':''}
def clone(value):return copy.deepcopy(value)
def settings(value):
    from export_policy import validate
    from input_color import CHOICES
    result={**DEFAULT,**value}
    choices={'input_mode':('single','strip','auto'),'working_space':('srgb','prophoto'),
             'method':('auto','sync','existing'),'sync_mode':('absolute','relative'),
             'group_rule':('manual','folder','tag','space','prefix'),'stop_after':('check','prepare','style')}
    for key,options in choices.items():
        if result[key] not in options:raise ValueError('批次设置无效：'+key)
    if result['input_color'] not in CHOICES:raise ValueError('输入色彩无效')
    if not isinstance(result['fields'],list) or any(x not in FIELDS for x in result['fields']):raise ValueError('同步字段无效')
    result['exposure_delta']=float(result['exposure_delta'])
    if not -4<=result['exposure_delta']<=4:raise ValueError('相对曝光应在 ±4 EV 内')
    for key in ('require_review','whole_roll','group_directories'):result[key]=bool(result[key])
    validate(result['template']);return {k:result[k] for k in DEFAULT}
def snapshot_key(snapshot):
    if snapshot is None:return None
    value={k:snapshot.get(k) for k in ('input','layout','frame_count','current_frame','frames','roll_params','options')}
    value['source']={k:snapshot['source'].get(k) for k in ('path','size','sha256')}
    return encoded(value)
def validate_payload(data):
    if not isinstance(data,dict) or data.get('format')!='film-invert-batch' or data.get('version')!=1:raise ValueError('批次版本不支持')
    settings(data['config'])
    if not isinstance(data['items'],list) or len(data['items'])>10000:raise ValueError('批次清单无效')
    ids=[x['asset'] for x in data['items']]
    if len(set(ids))!=len(ids) or any(len(x)!=32 or any(c not in '0123456789abcdef' for c in x) for x in ids):raise ValueError('批次照片编号无效')
    return data

class BatchStore:
    def __init__(self,studio):
        self.studio=studio;self.session=uuid.uuid4().hex
        with studio.connection() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS workflow_batches(id TEXT PRIMARY KEY,payload TEXT NOT NULL,updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS workflow_templates(id TEXT PRIMARY KEY,name TEXT NOT NULL,payload TEXT NOT NULL,updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS workflow_history(id TEXT PRIMARY KEY,batch_id TEXT NOT NULL,label TEXT NOT NULL,before_state TEXT NOT NULL,after_state TEXT NOT NULL,created REAL NOT NULL);''')
    def _read(self,db,identifier):
        row=db.execute('SELECT payload FROM workflow_batches WHERE id=?',(identifier,)).fetchone()
        if row is None:raise ValueError('批次不存在')
        return validate_payload(json.loads(row['payload']))
    def _save(self,db,data):
        data['updated']=time.time();payload=encoded(validate_payload(data))
        if len(payload.encode('utf-8'))>128*1024*1024:raise ValueError('批次记录超过 128 MiB')
        db.execute('INSERT OR REPLACE INTO workflow_batches(id,payload,updated) VALUES(?,?,?)',(data['id'],payload,data['updated']))
    def get(self,identifier):
        with self.studio.connection() as db:return self._read(db,identifier)
    def list(self):
        with self.studio.connection() as db:return [validate_payload(json.loads(r['payload'])) for r in db.execute('SELECT payload FROM workflow_batches ORDER BY updated DESC')]
    def create(self,identifiers,name='新批次'):
        items=[]
        for identifier in dict.fromkeys(identifiers):
            row=self.studio.get_asset(identifier)
            items.append({'asset':identifier,'name':row['name'],'group':row['roll'] or '未分组','selected':True,'skip':False,
                          'input_mode':None,'ev':0.,'stage':'imported','error':'','valid':False,'snapshot':None,
                          'origin':None,'preview_before':'','preview_after':'','job':None,'reviewed':False})
        data={'format':'film-invert-batch','version':1,'id':uuid.uuid4().hex,'name':str(name)[:120],
              'config':clone(DEFAULT),'items':items,'groups':{},'run':None,'report':None,'status':'idle'}
        with self.studio.connection() as db:self._save(db,data)
        return data['id']
    def edit(self,identifier,fn):
        with self.studio.connection() as db:
            db.execute('BEGIN IMMEDIATE');data=self._read(db,identifier)
            if data['status']=='running':raise ValueError('请先暂停当前批次处理')
            fn(data);self._save(db,data)
    def configure(self,identifier,value):
        config=settings(value)
        if config['output']:config['output']=str(Path(config['output']).expanduser().resolve())
        self.edit(identifier,lambda d:d.update(config=config))
    def update_items(self,identifier,ids,**values):
        allowed={'selected','skip','input_mode','group','ev'}
        if set(values)-allowed:raise ValueError('照片设置无效')
        if values.get('input_mode') not in (None,'single','strip','auto'):raise ValueError('输入方式无效')
        if 'ev' in values and not -4<=float(values['ev'])<=4:raise ValueError('独立曝光应在 ±4 EV 内')
        def apply(data):
            for item in data['items']:
                if item['asset'] in ids:
                    item.update(values);item['reviewed']=False
        self.edit(identifier,apply)
    def remove(self,identifier,ids):self.edit(identifier,lambda d:d.update(items=[x for x in d['items'] if x['asset'] not in ids]))
    def group(self,identifier,name,reference=None,share_base=False,confirmed=False):
        def apply(data):
            if reference and not any(i['asset']==reference and i['group']==name for i in data['items']):raise ValueError('参考图必须属于本组')
            data['groups'][name]={'reference':reference,'share_base':bool(share_base),'confirmed':bool(confirmed)}
        self.edit(identifier,apply)
    def group_by_rule(self,identifier,rule):
        def apply(data):
            if rule not in ('manual','folder','tag','space','prefix'):raise ValueError('分组规则无效')
            for item in data['items']:
                asset=self.studio.get_asset(item['asset'])
                item['group']=({'folder':Path(asset['path']).parent.name,'tag':asset['roll'],
                                'space':(item['snapshot'] or {}).get('input',{}).get('working_space','srgb'),
                                'prefix':Path(asset['name']).stem.replace('-','_').split('_')[0]}.get(rule,item['group']) or '未分组')[:120]
            data['config']['group_rule']=rule
            data['groups']={}
        self.edit(identifier,apply)
    def history(self,identifier):
        with self.studio.connection() as db:return [dict(r) for r in db.execute('SELECT id,label,created FROM workflow_history WHERE batch_id=? ORDER BY created DESC LIMIT 20',(identifier,))]
    def commit_item(self,identifier,item,asset_snapshot=None,label=None,expected_key=False):
        with self.studio.connection() as db:
            db.execute('BEGIN IMMEDIATE');data=self._read(db,identifier)
            index=next(i for i,x in enumerate(data['items']) if x['asset']==item['asset'])
            old=clone(data['items'][index]);asset=self.studio.asset(db.execute('SELECT * FROM assets WHERE id=?',(item['asset'],)).fetchone())
            if expected_key is not False and snapshot_key(asset['snapshot'])!=expected_key:raise ValueError('照片在处理期间被其他窗口编辑，已保留新编辑；请重新准备')
            if label:
                before={'item':old,'asset_snapshot':asset['snapshot'],'input_mode':asset['input_mode']}
                after={'item':item,'asset_snapshot':asset_snapshot,'input_mode':(asset_snapshot or {}).get('input',{}).get('mode',asset['input_mode'])}
                db.execute('INSERT INTO workflow_history VALUES(?,?,?,?,?,?)',(uuid.uuid4().hex,identifier,label,encoded(before),encoded(after),time.time()))
                db.execute('DELETE FROM workflow_history WHERE batch_id=? AND label NOT IN (SELECT label FROM workflow_history WHERE batch_id=? GROUP BY label ORDER BY MAX(created) DESC LIMIT 20)',(identifier,identifier))
            data['items'][index]=clone(item)
            if asset_snapshot is not None:
                db.execute('UPDATE assets SET snapshot=?,input_mode=?,updated=? WHERE id=?',(encoded(asset_snapshot),asset_snapshot['input']['mode'],time.time(),item['asset']))
            self._save(db,data)
    def undo(self,identifier):
        # One click undoes the latest operation across all files committed by it.
        with self.studio.connection() as db:
            db.execute('BEGIN IMMEDIATE');data=self._read(db,identifier)
            if data['status']=='running':raise ValueError('请先暂停处理')
            rows=db.execute('SELECT * FROM workflow_history WHERE batch_id=? ORDER BY created DESC',(identifier,)).fetchall()
            if not rows:return
            label=rows[0]['label'];rows=[r for r in rows if r['label']==label]
            simulated={}
            for r in rows:
                after=json.loads(r['after_state']);asset=self.studio.asset(db.execute('SELECT * FROM assets WHERE id=?',(after['item']['asset'],)).fetchone())
                key=asset['id'];current=simulated.get(key,asset['snapshot'])
                if snapshot_key(current)!=snapshot_key(after['asset_snapshot']):raise ValueError('照片在该操作后另有编辑，已保留；请先保存独立工程再撤销批次操作')
                simulated[key]=json.loads(r['before_state'])['asset_snapshot']
            for r in rows:
                before=json.loads(r['before_state']);item=before['item']
                for i,current in enumerate(data['items']):
                    if current['asset']==item['asset']:data['items'][i]=item;break
                db.execute('UPDATE assets SET snapshot=?,input_mode=?,updated=? WHERE id=?',
                           (encoded(before['asset_snapshot']) if before['asset_snapshot'] is not None else None,before['input_mode'],time.time(),item['asset']))
                db.execute('DELETE FROM workflow_history WHERE id=?',(r['id'],))
            self._save(db,data)
    def adopt(self,identifier,asset_id,snapshot):
        def apply(data):
            for item in data['items']:
                if item['asset']!=asset_id:continue
                if snapshot_key(item['snapshot'])==snapshot_key(snapshot):
                    item.update(snapshot=clone(snapshot),valid=True,verified_session=self.session)
                    if item['origin']:item['origin']['source']=clone(snapshot['source'])
                    return
                old=item['snapshot'];item.update(snapshot=clone(snapshot),valid=True,verified_session=self.session,error='',stage='prepared',reviewed=False)
                if item['origin'] is None:item['origin']=clone(snapshot)
                elif old:
                    item['origin']['source']=clone(snapshot['source'])
                    # A manual exposure adjustment remains an independent offset
                    # when reapplying a relative group correction.
                    for key,p in snapshot['frames'].items():
                        if key in old['frames'] and key in item['origin']['frames']:
                            item['origin']['frames'][key]['exposure']+=p['exposure']-old['frames'][key]['exposure']
        self.edit(identifier,apply)
    def preview(self,identifier,asset_id,path):
        def apply(data):
            for item in data['items']:
                if item['asset']==asset_id:item['preview_after']=str(path)
        self.edit(identifier,apply)
    def review(self,identifier,ids):
        def apply(data):
            for item in data['items']:
                if item['asset'] in ids and item['snapshot'] and item['valid'] and not item['error']:
                    item['reviewed']=True;item['stage']='reviewed'
        self.edit(identifier,apply)
    def start(self,identifier,operation,ids=None,overwrite=False):
        if operation not in ('check','prepare','style','pipeline','preview','report'):raise ValueError('处理步骤无效')
        token=uuid.uuid4().hex
        def apply(data):data.update(status='running',run={'id':token,'session':self.session,'operation':operation,'ids':ids,'overwrite':bool(overwrite),'cancel':False,'pid':None,'config':clone(data['config'])})
        self.edit(identifier,apply);return token
    def run_update(self,identifier,**values):
        with self.studio.connection() as db:
            db.execute('BEGIN IMMEDIATE');data=self._read(db,identifier)
            if data['run']:data['run'].update(values)
            self._save(db,data)
    def finish(self,identifier,status='paused'):
        with self.studio.connection() as db:
            db.execute('BEGIN IMMEDIATE');data=self._read(db,identifier);data['status']=status
            if data['run']:data['run']['pid']=None
            self._save(db,data)
    def cancelled(self,identifier):return bool((self.get(identifier)['run'] or {}).get('cancel'))
    def recover(self):
        try:
            with queue_lock(self.studio.folder/'batch-work'):
                for data in self.list():
                    if data['status']=='running':self.finish(data['id'])
        except QueueBusy:pass
    def templates(self):
        with self.studio.connection() as db:return [dict(r) for r in db.execute('SELECT id,name,payload FROM workflow_templates ORDER BY updated DESC')]
    def save_template(self,identifier,name):
        data=self.get(identifier);groups=clone(data['groups'])
        for value in groups.values():
            if value.get('reference'):
                item=next((i for i in data['items'] if i['asset']==value['reference']),None)
                if item and item['snapshot']:
                    s=item['snapshot'];value['values']=clone(s['frames'][str(s['current_frame'])])
            value.update(reference=None,share_base=False,confirmed=False)
        value={'format':'film-invert-batch-template','version':1,'config':data['config'],'groups':groups}
        template_id=uuid.uuid4().hex
        with self.studio.connection() as db:db.execute('INSERT INTO workflow_templates VALUES(?,?,?,?)',(template_id,str(name)[:120],encoded(value),time.time()))
        return template_id
    def apply_template(self,identifier,template_id):
        with self.studio.connection() as db:row=db.execute('SELECT payload FROM workflow_templates WHERE id=?',(template_id,)).fetchone()
        if row is None:raise ValueError('模板不存在')
        template=json.loads(row['payload'])
        if template.get('format')!='film-invert-batch-template' or template.get('version')!=1:raise ValueError('模板版本不支持')
        def apply(data):data.update(config=settings(template['config']),groups=clone(template['groups']))
        self.edit(identifier,apply)
    def new_round(self,identifier):
        def apply(data):
            old=[i['job'] for i in data['items'] if i.get('job')]
            if any(self.studio.get_job(j)['status'] in ('queued','running','paused') for j in old):raise ValueError('请先完成当前导出轮次；失败任务可保留并新建一轮')
            data.setdefault('rounds',[]).append({'jobs':old,'report':data.get('report'),'created':time.time()})
            for item in data['items']:item['job']=None;item['reviewed']=False;item['stage']='prepared' if item['snapshot'] else 'imported'
            data['report']=None;data.pop('report_error',None)
        self.edit(identifier,apply)
    def enqueue(self,identifier):
        from export_policy import names,options,clean
        data=self.get(identifier);config=data['config'];output=Path(config['output']).resolve() if config['output'] else None
        if data['status']=='running':raise ValueError('请等待批次步骤完成')
        revision=data['updated']
        if output is None:raise ValueError('请选择批次输出目录')
        targets=[i for i in data['items'] if i['selected'] and not i['skip'] and self.studio.get_asset(i['asset'])['flag']!='reject']
        problems=[i['name'] for i in targets if not i.get('job') and (not i['snapshot'] or not i['valid'] or i.get('verified_session')!=self.session or i['error'] or (config['require_review'] and not i['reviewed']))]
        if problems:raise ValueError('以下照片未准备、校验或复核：'+', '.join(problems[:20]))
        made=[];folder=output/('batch-'+clean(data['name'])+'-'+data['id'][:8])
        with self.studio.connection() as db:
            db.execute('BEGIN IMMEDIATE');data=self._read(db,identifier)
            if data['updated']!=revision:raise ValueError('批次状态已改变，请再次检查后加入队列')
            for item in data['items']:
                if item['asset'] not in {t['asset'] for t in targets}:continue
                if item.get('job') and db.execute('SELECT id FROM jobs WHERE id=?',(item['job'],)).fetchone():continue
                snapshot=clone(item['snapshot']);source=snapshot['source'];stat=io_path(source['path']).stat()
                if (stat.st_size,stat.st_mtime_ns)!=(source['size'],source['mtime_ns']):raise ValueError('原片有变化，请重新检查：'+item['name'])
                snapshot['_export']=options(config['template'],item['group']);snapshot['_queue_engine']=engine_signature()
                snapshot['_batch']={'id':identifier,'group':item['group'],'asset':item['asset']}
                indices=list(range(snapshot['frame_count'])) if config['whole_roll'] else [snapshot['current_frame']]
                names(source['path'],indices,snapshot['_export']);job=uuid.uuid4().hex
                group=(clean(item['group'])+'-'+hashlib.sha256(item['group'].encode()).hexdigest()[:6]) if config['group_directories'] else ''
                destination=folder/group/('film-'+clean(Path(item['name']).stem)+'-'+job)
                now=time.time();db.execute('INSERT INTO jobs(id,asset_id,name,snapshot,indices,destination,status,total_count,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (job,item['asset'],item['name'],encoded(snapshot),encoded(indices),str(destination),'queued',len(indices),now,now))
                item['job']=job;item['stage']='queued';made.append(job)
            data['export_root']=str(folder);self._save(db,data)
        return made
