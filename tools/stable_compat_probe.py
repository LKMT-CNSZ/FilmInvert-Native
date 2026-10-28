"""Synthetic rc13-to-stable probe; run using the respective shipped interpreter."""
from pathlib import Path
import json,os,sys,time
import film_invert_launcher as launcher
launcher.configure()
import desktop_model as model,native_app as ui,studio_worker
from studio_store import StudioStore,engine_signature
from tiffread import read_tiff
import numpy as np
from PySide6.QtCore import QTimer
mode,fixture=sys.argv[1:3];fixture=Path(fixture)
if mode=='baseline':
    app=ui.QApplication([]);source=fixture/'合成 原片.tif';profile=fixture/'rc13-data'
    opened=model.open_image(source,input_mode='single');p=opened['params'];p.update(exposure=.37,straighten=.4,rotation=90,local_edits=[dict(kind='brush',points=[[.5,.5]],radius=.07,ev=.2)])
    p=model.params(p).__dict__
    model.preview(p);snapshot=model.project_snapshot(p)
    model.write_json(fixture/'rc13.filmproj',snapshot);model.write_json(fixture/'rc13-recipe.json',model.recipe(p))
    model.write_json(profile/'session.json',snapshot)
    store=StudioStore(profile);identifier=store.capture(snapshot);store.metadata(identifier,4,'pick','合成测试卷')
    job=store.enqueue([identifier],fixture/'old-queue-output',False)[0]
    model.export_image(fixture/'rc13-positive.tif',p)
    model.write_json(fixture/'baseline.json',{'application':model.VERSION,'params':p,'asset':identifier,'job':job})
    print('PASS rc13 synthetic project/recipe/session/library/frozen-job fixture created')
else:
    from app_paths import select_profile,default_output
    baseline=json.loads((fixture/'baseline.json').read_text('utf-8'));profile=select_profile(ui.APP_DIR)
    if mode=='portable':
        assert profile==ui.APP_DIR/'data' and default_output(ui.APP_DIR,profile)==ui.APP_DIR/'out'
    else:
        assert profile==Path(os.environ['LOCALAPPDATA'])/'FilmInvertNative/data'
        assert default_output(ui.APP_DIR,profile)==Path(os.environ['LOCALAPPDATA'])/'FilmInvertNative/exports'
    assert model.VERSION in ('3.0.0','3.0.1')
    opened=model.open_project(fixture/'rc13.filmproj');assert opened['params']==baseline['params']
    assert model.recipe_params(json.loads((fixture/'rc13-recipe.json').read_text('utf-8')))==baseline['params']
    store=StudioStore(profile);asset=store.get_asset(baseline['asset']);assert asset['rating']==4 and asset['flag']=='pick' and asset['roll']=='合成测试卷'
    restored=store.open_asset(asset['id']);assert restored['params']==baseline['params']
    output=fixture/'stable-positive.tif';model.export_image(output,restored['params'])
    actual,expected=read_tiff(str(output)),read_tiff(str(fixture/'rc13-positive.tif'))
    assert np.array_equal(actual,expected)
    job=store.get_job(baseline['job']);assert job['snapshot']['_queue_engine']!=engine_signature()
    try:studio_worker.execute(store,job['id'])
    except ValueError as error:assert '引擎' in str(error)
    else:raise AssertionError('old frozen job silently changed engine')
    # Exercise the real launcher -> native_app.main -> event loop -> restored session.
    Base=ui.MainWindow
    class SmokeWindow(Base):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs);self.restore_session();self.deadline=time.monotonic()+80
            self.poll=QTimer(self);self.poll.timeout.connect(self.check);self.poll.start(30)
        def check(self):
            if time.monotonic()>self.deadline:raise AssertionError('session GUI timeout')
            if not self.loaded or self.job is not None or self.preview_timer.isActive() or any(j.isRunning() for j in self.jobs):return
            assert self.p==baseline['params'];assert self.isVisible();self.poll.stop()
            self.save_session();model.write_json(fixture/'native-launch.json',{'status':'pass','version':model.VERSION,'profile_outside_install':not self.profile.is_relative_to(ui.APP_DIR),'mode':mode,'session_restored':True,'platform':self.windowHandle().screen().name(),'full_uint16_samples_equal':int(actual.size)})
            self.close()
    ui.MainWindow=SmokeWindow
    sys.argv=['FilmInvert','--profile',str(profile)]
    assert launcher.main()==0
    assert (fixture/'native-launch.json').is_file()
    print('PASS installed launcher/event loop, rc13 project/recipe/library/session restore, exact uint16 TIFF, old frozen queue refusal')
