"""Regression tests for chart objective, actual clipping, frozen holdouts and UI roles."""
from pathlib import Path
import os,sys,tempfile,copy,io
os.environ['QT_QPA_PLATFORM']='offscreen'
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import native_app,calibration,calibration_solver as solver,chart_metrics,working_color
import numpy as np
from native_calibration import CalibrationDialog

app=native_app.QApplication.instance() or native_app.QApplication([])
rng=np.random.default_rng(720)
y=rng.uniform(.04,.65,(24,3));mix=np.array([[.88,.04,.01],[.02,1.05,.01],[.01,.02,.92]])
x=y@mix.T;points=[[.5,.5]]*24
refs=[{'name':str(i),'rgb':(calibration.encode(v)*255).tolist(),'role':'validation' if i>=18 else 'train'} for i,v in enumerate(y)]
profile=calibration.fit(refs,x.tolist(),[0]*24,points)
assert profile['report']['can_apply'],profile['report']
assert profile['report']['valid']==18 and profile['report']['validation']['valid']==6
assert np.max(np.abs(np.array(profile['matrix'])-np.linalg.inv(mix)))<.001
# Changing holdout references cannot alter fitting or LOO, but must reject harmful output.
bad=copy.deepcopy(refs)
for i in range(18,24):bad[i]['rgb']=(calibration.encode(x[i])*255).tolist()
rejected=calibration.fit(bad,x.tolist(),[0]*24,points)
assert rejected['matrix']==profile['matrix'] and rejected['report']['cv_rmse']==profile['report']['cv_rmse']
assert not rejected['report']['can_apply'] and not rejected['report']['validation']['passed']
clipped=[0]*24;clipped[20:]=[1]*4
try:calibration.fit(refs,x.tolist(),clipped,points)
except ValueError as e:assert '至少需要 3' in str(e)
else:raise AssertionError('Too few holdouts accepted')
# Solver derivative includes both working-space and output clipping.
for space in ('srgb','prophoto'):
    matrix=np.array([[1.2,-.2,.05],[.04,.94,.02],[-.1,.03,1.3]])
    test=rng.uniform(.01,.98,(20,3));prediction,jac=solver.predict_jacobian(test,matrix,space)
    for j in range(9):
        delta=np.zeros((3,3));delta.flat[j]=1e-6
        finite=(solver.render(test@(matrix+delta).T,space)-solver.render(test@(matrix-delta).T,space)).ravel()/2e-6
        assert np.max(np.abs(finite-jac[:,j]))<1e-6
    actual=calibration.apply(test.astype(np.float32),{'matrix':matrix.tolist(),'working_space':space},working_space=space)
    assert np.allclose(prediction,working_color.display(actual,space),atol=2e-6)
    target=working_color.linear(y.astype(np.float32),'srgb',space)
    measured=working_color.linear(x.astype(np.float32),'srgb',space)
    fitted=solver.solve(measured,target,space)
    assert np.mean((solver.render(measured@fitted.T,space)-solver.render(target,space))**2)<np.mean((solver.render(measured,space)-solver.render(target,space))**2)
# CSV role roundtrip, strict schema and existing profile pixels remain intact.
(ROOT/'build'/'tests').mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(dir=ROOT/'build'/'tests') as folder:
    path=Path(folder)/'roles.csv'
    path.write_text('name,r,g,b,role\n'+'\n'.join(f'{i},100,120,140,{"validation" if i>=8 else "train"}' for i in range(11)),encoding='utf-8')
    assert [v['role'] for v in calibration.read_reference(path)]==['train']*8+['validation']*3
    path.write_text(path.read_text().replace('validation','wrong'),encoding='utf-8')
    try:calibration.read_reference(path)
    except ValueError as e:assert 'role' in str(e)
    else:raise AssertionError('Bad role accepted')
old={'matrix':profile['matrix'],'reference':[dict(name=r['name'],rgb=r['rgb']) for r in refs],'points':points}
assert np.array_equal(calibration.apply(x,old),calibration.apply(x,calibration.normalize_profile(old)))
# Native editor preserves validation flags and invalidates results when role changes.
preview={'linear':x.reshape(4,6,3).astype(np.float32),'working_space':'srgb','source_sha256':'x','frame':0,'parameter_signature':'p'}
profile.update(source_sha256='x',frame=0,parameter_signature='p')
dialog=CalibrationDialog(preview,profile,ROOT/'samples')
assert dialog.apply_button.isEnabled() and '独立验证 6 色' in dialog.report.text()
dialog.table.setCurrentCell(18,0);assert dialog.validation_patch.isChecked()
dialog.validation_patch.setChecked(False)
assert dialog.result is None and not dialog.apply_button.isEnabled() and dialog.reference[18]['role']=='train'
dialog.set_busy(True);assert not dialog.validation_patch.isEnabled()
dialog.set_busy(False);dialog.set_result(rejected);assert not dialog.apply_button.isEnabled()
dialog.reject()
# Sharma/Wu/Dalal (2005) supplementary published pairs, including hue wrap edge cases.
# https://hajim.rochester.edu/ece/sites/gsharma/ciede2000/
SHARMA_DATA='''50.0000	2.6772	-79.7751	50.0000	0.0000	-82.7485	2.0425
50.0000	3.1571	-77.2803	50.0000	0.0000	-82.7485	2.8615
50.0000	2.8361	-74.0200	50.0000	0.0000	-82.7485	3.4412
50.0000	-1.3802	-84.2814	50.0000	0.0000	-82.7485	1.0000
50.0000	-1.1848	-84.8006	50.0000	0.0000	-82.7485	1.0000
50.0000	-0.9009	-85.5211	50.0000	0.0000	-82.7485	1.0000
50.0000	0.0000	0.0000	50.0000	-1.0000	2.0000	2.3669
50.0000	-1.0000	2.0000	50.0000	0.0000	0.0000	2.3669
50.0000	2.4900	-0.0010	50.0000	-2.4900	0.0009	7.1792
50.0000	2.4900	-0.0010	50.0000	-2.4900	0.0010	7.1792
50.0000	2.4900	-0.0010	50.0000	-2.4900	0.0011	7.2195
50.0000	2.4900	-0.0010	50.0000	-2.4900	0.0012	7.2195
50.0000	-0.0010	2.4900	50.0000	0.0009	-2.4900	4.8045
50.0000	-0.0010	2.4900	50.0000	0.0010	-2.4900	4.8045
50.0000	-0.0010	2.4900	50.0000	0.0011	-2.4900	4.7461
50.0000	2.5000	0.0000	50.0000	0.0000	-2.5000	4.3065
50.0000	2.5000	0.0000	73.0000	25.0000	-18.0000	27.1492
50.0000	2.5000	0.0000	61.0000	-5.0000	29.0000	22.8977
50.0000	2.5000	0.0000	56.0000	-27.0000	-3.0000	31.9030
50.0000	2.5000	0.0000	58.0000	24.0000	15.0000	19.4535
50.0000	2.5000	0.0000	50.0000	3.1736	0.5854	1.0000
50.0000	2.5000	0.0000	50.0000	3.2972	0.0000	1.0000
50.0000	2.5000	0.0000	50.0000	1.8634	0.5757	1.0000
50.0000	2.5000	0.0000	50.0000	3.2592	0.3350	1.0000
60.2574	-34.0099	36.2677	60.4626	-34.1751	39.4387	1.2644
63.0109	-31.0961	-5.8663	62.8187	-29.7946	-4.0864	1.2630
61.2901	3.7196	-5.3901	61.4292	2.2480	-4.9620	1.8731
35.0831	-44.1164	3.7933	35.0232	-40.0716	1.5901	1.8645
22.7233	20.0904	-46.6940	23.0331	14.9730	-42.5619	2.0373
36.4612	47.8580	18.3852	36.2715	50.5065	21.2231	1.4146
90.8027	-2.0831	1.4410	91.1528	-1.6435	0.0447	1.4441
90.9257	-0.5406	-0.9208	88.6381	-0.8985	-0.7239	1.5381
6.7747	-0.2908	-2.4247	5.8714	-0.0985	-2.2286	0.6377
2.0776	0.0795	-1.1350	0.9033	-0.0636	-0.5514	0.9082

'''
data=np.loadtxt(io.StringIO(SHARMA_DATA))
assert len(data)==34 and np.max(np.abs(chart_metrics.de00(data[:,:3],data[:,3:6])-data[:,6]))<.00005
print('PASS chart validation: rendered objective/Jacobian, wide clipping parity, frozen holdout rejection, CSV/UI roles, legacy profile pixels, 34 published Delta E00 pairs')

