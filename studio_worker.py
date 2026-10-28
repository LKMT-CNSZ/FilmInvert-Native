"""One isolated export task. Durable per-frame intents survive interruption."""
from pathlib import Path
import argparse
import json
import os
import sys
import traceback
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parent))
if (Path(__file__).resolve().parent.parent/'portable-layout.json').is_file():
    from film_invert_launcher import configure
    configure()
from studio_store import StudioStore,queue_lock,sha256,QueueBusy,engine_signature
from file_io import io_path


def publish(stage,destination,expected):
    """Never replace an existing output, even during retry/recovery."""
    stage,destination=io_path(stage),io_path(destination)
    if destination.exists():
        if sha256(destination)!=expected:raise ValueError('已完成的输出被修改，已保留文件；请新建导出任务：'+str(destination))
        return
    if not stage.is_file() or sha256(stage)!=expected:raise ValueError('缺少或改动了导出暂存文件，请新建任务：'+str(stage))
    if os.name=='nt':os.rename(stage,destination)  # Windows rename fails if target exists.
    else:os.link(stage,destination);stage.unlink()


def recover_output(store,job,index,record):
    directory=Path(job['destination'])
    for key in ('tiff','recipe'):
        part=record[key]
        publish(directory/part['stage'],directory/part['name'],part['sha256'])
    if record['state']!='done':
        record['state']='done';store.checkpoint(job['id'],index,record)


def frame_parameters(snapshot,index):
    import core
    import desktop_model as model
    import strip
    engine=model.engine;layout=engine.STATE['strip_layout'];saved=snapshot['frames']
    if layout is None:return engine.STATE['full'],model.params(saved[str(snapshot['current_frame'])])
    si,_,array=strip.extract_one(engine.STATE['strip_img'],layout,index)
    if str(index) in saved:p=model.params(saved[str(index)])
    else:
        roll=snapshot.get('roll_params');p=model.params(roll or {});p.base,p.anchor=layout.bases[si],None
        if snapshot['options']['auto_meter'] or roll is None:_,p.span=core.auto_anchor(array,p.base)
    p=engine.prepare_params(p,engine.STATE['strip_img'],layout,index)
    if str(index) not in saved and p.flat_mode=='strip':
        p.base=engine.strip_field(engine.STATE['strip_img'],layout,index)[1]
        if snapshot['options']['auto_meter']:_,p.span=core.auto_anchor(engine.corrected_input(array,p),p.base)
    p.output_space=saved[str(snapshot['current_frame'])].get('output_space','srgb')
    return array,p


def execute(store,identifier,progress=lambda *args:None):
    import desktop_model as model
    with queue_lock(store.folder):
        store.claim(identifier);job=store.get_job(identifier)
        try:
            if store.cancelled(identifier):store.status(identifier,'paused');return 'paused'
            snapshot=job['snapshot']
            if snapshot.get('_queue_engine')!=engine_signature():raise ValueError('处理引擎或依赖版本已改变，请用当前软件新建导出任务')
            # Existing project loader verifies the source hash and frame layout.
            model.open_project(store.materialize(snapshot,'job-'+identifier))
            from export_policy import names
            filenames=names(snapshot['source']['path'],job['indices'],snapshot.get('_export'))
            directory=io_path(job['destination']);directory.mkdir(parents=True,exist_ok=True)
            for number,index in enumerate(job['indices']):
                if store.cancelled(identifier):store.status(identifier,'paused');return 'paused'
                record=store.get_job(identifier)['outputs'].get(str(index))
                if record is None:
                    array,p=frame_parameters(snapshot,index)
                    name=filenames[number];final=directory/name
                    if final.exists() or Path(str(final)+'.json').exists():raise ValueError('目标文件已经存在，已保留文件；请新建任务：'+str(final))
                    stage=directory/('.pending-'+uuid.uuid4().hex+'.tif')
                    recipe=Path(str(stage)+'.json')
                    try:
                        model.export_image(stage,p.__dict__,array,snapshot['source']['path'],index+1)
                        # Flush both files before publishing a durable intent.
                        for path in (stage,recipe):
                            with path.open('r+b') as stream:os.fsync(stream.fileno())
                        record={'state':'prepared','frame':index+1,'params':p.__dict__,'source_region':model.source_region(index),
                            'tiff':{'stage':stage.name,'name':name,'sha256':sha256(stage)},
                            'recipe':{'stage':recipe.name,'name':name+'.json','sha256':sha256(recipe)}}
                    except Exception:
                        # No durable intent yet: these files cannot be recovered.
                        stage.unlink(missing_ok=True)
                        recipe.unlink(missing_ok=True)
                        raise
                    store.checkpoint(identifier,index,record)
                recover_output(store,job,index,record)
                progress(number+1,len(job['indices']),str(directory))
            # Final verification covers all earlier completed frames as well.
            outputs=store.get_job(identifier)['outputs']
            model.write_json(directory/'manifest.json',{'format':'film-invert-queued-export','job_id':identifier,
                'source':snapshot['source'],'input':snapshot.get('input'),'frames':[outputs[str(i)] for i in job['indices']]})
            store.status(identifier,'completed');return 'completed'
        except Exception as error:
            store.status(identifier,'failed',str(error));raise


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--profile',required=True);parser.add_argument('--job',required=True)
    args=parser.parse_args();store=StudioStore(args.profile)
    try:
        def progress(done,total,path):print(json.dumps({'done':done,'total':total}),flush=True)
        execute(store,args.job,progress);return 0
    except QueueBusy as error:
        print(str(error),file=sys.stderr);return 75
    except Exception:
        traceback.print_exc();return 1

if __name__=='__main__':raise SystemExit(main())
