"""Run existing meaningful integration tests using only the shipped interpreter."""
from pathlib import Path
import json
import os
import subprocess
import sys
sys.stdout.reconfigure(encoding='utf-8')

ROOT=Path(__file__).resolve().parents[1]
version=(ROOT/'VERSION.txt').read_text('utf-8').strip()
bundle=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else ROOT/'dist'/f'{version}-compat'/'FilmInvertNative'
foreign=ROOT/'build'/'compat-tests'/'中文 空格 工作目录';foreign.mkdir(parents=True,exist_ok=True)
temp=foreign/'temp';temp.mkdir(exist_ok=True)
env=dict(os.environ)
env.update(PYTHONHOME=str(foreign/'not-a-python'),PYTHONPATH=str(foreign/'not-a-module'),
           PYTHONIOENCODING='utf-8',TEMP=str(temp),TMP=str(temp))
windows=Path(os.environ.get('WINDIR','C:/Windows'))
env['PATH']=str(windows/'System32')+os.pathsep+str(windows)
python=str(bundle/'runtime'/'python.exe')
commands=[['-I','-X','utf8','-m','film_invert_launcher','--diagnose']]
commands += [['-I','-X','utf8',str(bundle/'app'/'tools'/name)] for name in
             ('selfcheck.py','test_native.py','test_project.py','test_chart.py','test_chart_validation.py','test_viewport.py','test_region_context.py','test_geometry.py','test_frames.py','test_frame_detection.py','test_studio.py','test_workflow.py','test_input_color.py','test_wide_color.py','test_output_color.py','test_local.py','test_delivery.py','test_streaming.py','test_long_paths.py','test_navigation.py','test_strip_base.py','test_line_straighten.py','test_strip_alignment.py','test_perspective.py','test_darkroom.py','test_large_film.py','test_multi_drop.py','test_photo_navigation.py','test_batch_workflow.py','test_batch_ui.py','test_switch_cache.py','test_efficiency.py','test_release_paths.py','test_i18n.py','test_metering.py','test_tone.py','test_hdr_merge.py','test_hdr_display.py','test_hdr_ui.py','test_hdr_canvas.py','test_hdr_jxr.py','test_import_reveal.py','test_startup.py','test_hdr_workbench.py','test_hdr_presets_ultra.py','test_installer_upgrade.py')]
results=[]
for args in commands:
    result=subprocess.run([python,*args],cwd=foreign,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                          encoding='utf-8',errors='replace',timeout=150)
    label=args[-1];print(label,':',result.returncode,flush=True)
    if result.returncode:
        print(result.stdout);raise SystemExit(result.returncode)
    lines=[line for line in result.stdout.splitlines() if line.startswith('PASS') or '全部通过' in line]
    if lines:print('\n'.join(lines),flush=True)
    results.append({'test':label,'exit_code':result.returncode})
(ROOT/'build'/'compat-tests'/'results.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
print('PASS isolated shipped runtime, foreign cwd, invalid system Python variables, all native/project/chart tests')
