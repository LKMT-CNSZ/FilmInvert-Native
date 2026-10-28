"""Compare disabled-calibration output with a separately supplied prior core.py."""
from pathlib import Path
import sys
import importlib.util
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import core

baseline=Path(sys.argv[1]).resolve()
spec=importlib.util.spec_from_file_location('prior_core',baseline)
old=importlib.util.module_from_spec(spec);sys.modules['prior_core']=old;spec.loader.exec_module(old)
rng=np.random.default_rng(20261022);image=(rng.random((97,131,3))*.7+.01).astype(np.float32)
cases=[]
for mode in ('positive','color_negative','bw_negative'):
    for settings in ({},{'exposure':.35,'flip':True,'temp':.2,'tint':-.1},
                     {'film':'kodak_portra_400','crosstalk':.3,'curve':[[0,0],[.3,.2],[.7,.8],[1,1]],'hsl':[[.2,-.1]]+[[0,0]]*7},
                     {'wb_gain':[1.1,.95,1.05],'dust_strength':.4,'vignette':.2}):
        values=dict(mode=mode,base=[.7,.5,.3],**settings)
        actual=core.process(image,core.Params(**values));expected=old.process(image,old.Params(**values))
        assert np.array_equal(actual,expected),(mode,settings,np.max(np.abs(actual-expected)))
        cases.append(values)
print(f'PASS core compatibility: {len(cases)} cases exactly match {baseline}')
