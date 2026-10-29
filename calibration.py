"""Linear RGB chart fitting. References are explicit user-provided sRGB values."""
import csv
import copy
import hashlib
import io
import json
from pathlib import Path
import numpy as np


def valid_matrix(value):
    try:
        matrix=np.asarray(value,dtype=np.float64)
        if matrix.shape!=(3,3) or not np.isfinite(matrix).all() or np.max(np.abs(matrix))>4:
            raise ValueError()
        if np.linalg.cond(matrix)>30:raise ValueError()
        return matrix.astype(np.float32).tolist()
    except (TypeError,ValueError,np.linalg.LinAlgError):
        return None


def normalize_profile(value):
    if not isinstance(value,dict) or valid_matrix(value.get('matrix')) is None:return None
    # Profiles created by this app contain only bounded reference/measurement data.
    try:
        data=json.loads(json.dumps(value,allow_nan=False))
        if len(json.dumps(data))>150000:return None
        data['matrix']=valid_matrix(data['matrix'])
        if data.get('working_space','srgb') not in ('srgb','prophoto'):return None
        reference=data.get('reference')
        if reference is not None:
            if not isinstance(reference,list) or not 8<=len(reference)<=96:return None
            for patch in reference:
                if not isinstance(patch,dict) or not isinstance(patch.get('name'),str):return None
                if patch.get('role','train') not in ('train','validation'):return None
                rgb=np.asarray(patch.get('rgb'),dtype=np.float64)
                if rgb.shape!=(3,) or not np.isfinite(rgb).all() or rgb.min()<0 or rgb.max()>255:return None
            points=data.get('points')
            if not isinstance(points,list) or len(points)!=len(reference):return None
            for point in points:
                if point is None:continue
                point=np.asarray(point,dtype=np.float64)
                if point.shape!=(2,) or not np.isfinite(point).all() or point.min()<0 or point.max()>1:return None
        report=data.get('report')
        if report is not None:
            if not isinstance(report,dict):return None
            for key in ('before_rmse','after_rmse','cv_rmse'):
                if key not in report or not np.isfinite(float(report[key])) or float(report[key])<0:return None
        return data
    except (ValueError,TypeError):return None


def decode(rgb):
    x=np.asarray(rgb,np.float64)
    return np.where(x<=.04045,x/12.92,((x+.055)/1.055)**2.4)


def encode(rgb):
    x=np.clip(np.asarray(rgb,np.float64),0,1)
    return np.where(x<=.0031308,x*12.92,1.055*x**(1/2.4)-.055)


def apply(rgb,profile,strength=1.,working_space='srgb'):
    if not profile or strength<=0:return rgb
    if profile.get('working_space','srgb')!=working_space:raise ValueError('色卡配置与当前工作空间不一致，请重新标定')
    matrix=np.asarray(profile['matrix'],np.float32)
    if np.array_equal(matrix,np.eye(3,dtype=np.float32)):return rgb
    matrix=np.eye(3,dtype=np.float32)+np.float32(strength)*(matrix-np.eye(3,dtype=np.float32))
    return np.clip(rgb@matrix.T,0,1).astype(np.float32)


def read_reference(path):
    path=Path(path)
    if path.stat().st_size>256*1024:raise ValueError('色卡参考文件超过 256 KB')
    rows=list(csv.DictReader(io.StringIO(path.read_text('utf-8-sig'))))
    if not 8<=len(rows)<=96:raise ValueError('参考 CSV 需要 8–96 个色块')
    result=[]
    for i,row in enumerate(rows):
        try:
            rgb=[float(row[c]) for c in ('r','g','b')]
            if not np.isfinite(rgb).all() or min(rgb)<0 or max(rgb)>255:raise ValueError()
        except (TypeError,ValueError,KeyError):
            raise ValueError(f'第 {i+1} 行参考无效，需要 name,r,g,b 表头及 0–255 sRGB 数值') from None
        role=str(row.get('role') or 'train').strip().lower()
        if role not in ('train','validation'):raise ValueError(f'第 {i+1} 行 role 需要 train 或 validation')
        result.append({'name':str(row.get('name') or f'色块 {i+1}')[:80],'rgb':rgb,'role':role})
    return result


def parameter_signature(params):
    value=copy.deepcopy(params);value.pop('calibration',None);value.pop('calibration_strength',None)
    if value.get('working_space')=='srgb':value.pop('working_space',None)
    # Output gamut does not affect the sRGB working stage used for measurements.
    value.pop('output_space',None)
    # Geometry follows the chart measurement stage; old sampling stays valid.
    for name in ('rotation','straighten','crop','perspective'):value.pop(name,None)
    # New neutral controls must not invalidate a historical chart measurement.
    defaults={'meter_mode':'legacy','scene_hdr':False,'tone_shadows':0.,'tone_highlights':0.,
        'tone_whites':0.,'tone_blacks':0.,'tone_rolloff':0.,'local_tone':0.,'local_radius':.03,'local_detail':0.}
    for key,default in defaults.items():
        if value.get(key,default)==default:value.pop(key,None)
    return hashlib.sha256(json.dumps(value,sort_keys=True,allow_nan=False).encode()).hexdigest()


def sample_blocks(linear,points,radius=5):
    h,w=linear.shape[:2];samples=[];clipped=[]
    for point in points:
        if point is None:samples.append(None);clipped.append(1.);continue
        if len(point)!=2 or not np.isfinite(point).all() or min(point)<0 or max(point)>1:
            raise ValueError('取样坐标无效')
        x,y=point;px=min(w-1,int(x*w));py=min(h-1,int(y*h))
        block=linear[max(0,py-radius):min(h,py+radius+1),max(0,px-radius):min(w,px+radius+1)]
        samples.append(np.median(block.reshape(-1,3),axis=0).tolist())
        clipped.append(float(np.mean(np.any((block<=1e-5)|(block>=1-1e-5),axis=2))))
    return samples,clipped


def fit(reference,samples,clipped,points,name='自定义色卡',working_space='srgb'):
    if len(reference)!=len(samples) or len(samples)!=len(clipped) or len(points)!=len(reference):
        raise ValueError('参考色块与取样数量不一致')
    roles=[p.get('role','train') for p in reference]
    if any(r not in ('train','validation') for r in roles):raise ValueError('色块用途需要 train 或 validation')
    usable=[i for i,s in enumerate(samples) if s is not None and clipped[i]<=.1]
    indexes=[i for i in usable if roles[i]=='train']
    held=[i for i in usable if roles[i]=='validation']
    if len(indexes)<8:raise ValueError('至少需要 8 个有效色块；请补齐取样，避开边缘、反光和剪裁区域')
    if 'validation' in roles and len(held)<3:raise ValueError('独立验证至少需要 3 个有效色块；请补齐验证取样或调整用途')
    x=np.asarray([samples[i] for i in indexes],np.float64)
    y=decode(np.asarray([reference[i]['rgb'] for i in indexes],np.float64)/255.)
    if working_space=='prophoto':
        from working_color import linear
        y=linear(y)
    if x.shape!=(len(indexes),3) or not np.isfinite(x).all() or np.linalg.matrix_rank(x)<3 or np.linalg.cond(x)>100:
        raise ValueError('色块颜色变化不足，无法稳定拟合；请加入不同色相的色块')
    import calibration_solver
    def solve(a,b):return calibration_solver.solve(a,b,working_space)
    matrix=solve(x,y)
    if valid_matrix(matrix) is None:raise ValueError('校正矩阵过强或不稳定，请检查参考值与色块对应关系')
    predictions=x@matrix.T
    cross=[]
    for i in range(len(x)):
        mask=np.arange(len(x))!=i
        if np.linalg.matrix_rank(x[mask])<3:raise ValueError('样本过于集中，留一验证无法稳定计算')
        cross.append(x[i]@solve(x[mask],y[mask]).T)
    def encoded(a):return calibration_solver.render(a,working_space)
    error=lambda a,b:float(np.sqrt(np.mean(((encoded(a)-encoded(b))*255)**2)))
    before=error(x,y);after=error(predictions,y);cv=error(cross,y)
    per_before=np.sqrt(np.mean(((encoded(x)-encoded(y))*255)**2,axis=1))
    per_after=np.sqrt(np.mean(((encoded(predictions)-encoded(y))*255)**2,axis=1))
    from chart_metrics import lab_from_encoded,de00,summary
    target_lab=lab_from_encoded(encoded(y))
    delta=lambda a:de00(lab_from_encoded(encoded(a)),target_lab)
    de_before=summary(delta(x));de_after=summary(delta(predictions));de_cv=summary(delta(cross))
    # Frozen holdouts approve/reject only: neither optimization nor LOO sees them.
    validation=None
    if held:
        hx=np.asarray([samples[i] for i in held],np.float64)
        hy=decode(np.asarray([reference[i]['rgb'] for i in held],np.float64)/255.)
        if hx.shape!=(len(held),3) or not np.isfinite(hx).all():raise ValueError('验证取样无效')
        if working_space=='prophoto':hy=linear(hy)
        hp=hx@matrix.T;hl=lab_from_encoded(encoded(hy))
        hb=summary(de00(lab_from_encoded(encoded(hx)),hl));ha=summary(de00(lab_from_encoded(encoded(hp)),hl))
        vb=error(hx,hy);va=error(hp,hy)
        validation={'valid':len(held),'indexes':held,'before_rmse':vb,'after_rmse':va,
                    'before_de00':hb,'after_de00':ha,
                    'passed':va<=vb+1e-5 and ha['mean']<=hb['mean']+1e-5 and ha['p95']<=hb['p95']+1e-5,
                    'per_patch_before':np.sqrt(np.mean(((encoded(hx)-encoded(hy))*255)**2,axis=1)).tolist(),
                    'per_patch_after':np.sqrt(np.mean(((encoded(hp)-encoded(hy))*255)**2,axis=1)).tolist()}
    reasons=[]
    if after>before+1e-5:reasons.append('训练 RGB 误差恶化')
    if cv>before*1.02+.05:reasons.append('留一 RGB 误差恶化')
    # An explicit holdout is the perceptual approval set; otherwise LOO is the fallback.
    if validation is None and de_cv['mean']>de_before['mean']+1e-5:reasons.append('留一平均 ΔE00 恶化')
    if validation and not validation['passed']:reasons.append('独立验证 RGB / 平均或 P95 ΔE00 恶化')
    can_apply=not reasons
    return {'format':'film-invert-calibration','version':2 if working_space=='prophoto' else 1,'name':str(name)[:100],
            'working_space':working_space,
            'matrix':valid_matrix(matrix),'reference':copy.deepcopy(reference),'points':copy.deepcopy(points),
            'measurements':samples,'clipped_fractions':clipped,
            'report':{'valid':len(indexes),'total':len(reference),'used_indexes':indexes,
                      'before_rmse':before,'after_rmse':after,'cv_rmse':cv,'can_apply':can_apply,
                      'fit_method':'encoded-rgb-lm-v1','metric':'encoded sRGB RMSE, 0-255; working clip before conversion','training_not_worse':after<=before+1e-5,
                      'before_de00':de_before,'after_de00':de_after,'cv_de00':de_cv,
                      'delta_e_reference':'sRGB-derived Lab D50, nominal reference; not device certification',
                      'validation':validation,'rejection_reasons':reasons,
                      'per_patch_before':per_before.tolist(),'per_patch_after':per_after.tolist()}}
