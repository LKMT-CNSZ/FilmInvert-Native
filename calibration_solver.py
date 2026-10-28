"""Bounded matrix fitting in the same clipped/encoded domain used for chart review."""
import numpy as np
import colorio

def transform(space):
    if space=='srgb':return np.eye(3,dtype=np.float64)
    adaptation=np.linalg.inv(colorio.ADAPT)
    return (np.linalg.inv(colorio.MATRICES['srgb'])@adaptation@colorio.MATRICES[space]).astype(np.float32).astype(np.float64)

def render(rgb,space='srgb'):
    # Apply clips in working RGB before converting to the common sRGB metric.
    linear=np.clip(np.asarray(rgb,np.float64),0,1)@transform(space).T
    x=np.clip(linear,0,1)
    return np.where(x<=.0031308,x*12.92,1.055*x**(1/2.4)-.055)

def predict_jacobian(x,matrix,space):
    raw=x@matrix.T;work=np.clip(raw,0,1);t=transform(space);lin=work@t.T;v=np.clip(lin,0,1)
    encoded=np.where(v<=.0031308,v*12.92,1.055*v**(1/2.4)-.055)
    derivative=np.where(v<=.0031308,12.92,(1.055/2.4)*np.maximum(v,1e-12)**(1/2.4-1))
    derivative*=((lin>0)&(lin<1))
    active=((raw>0)&(raw<1)).astype(np.float64)
    jac=np.einsum('nc,cd,nd,nj->ncdj',derivative,t,active,x).reshape(-1,9)
    return encoded,jac

def solve(x,y,space='srgb',max_iterations=24):
    x,y=np.asarray(x,np.float64),np.asarray(y,np.float64)
    identity=np.eye(3);target=render(y,space);ridge=len(x)*1e-6
    penalty=max(float(np.trace(x.T@x))/3*1e-5,1e-9)
    linear=np.linalg.solve(x.T@x+penalty*identity,x.T@y+penalty*identity).T
    def valid(m):
        return bool(np.isfinite(m).all() and np.max(np.abs(m))<=4 and np.linalg.cond(m)<=30)
    def score(m):return float(np.sum((render(x@m.T,space)-target)**2)+ridge*np.sum((m-identity)**2))
    m=linear if valid(linear) and score(linear)<score(identity) else identity.copy()
    best=score(m);damping=1e-4
    for _ in range(max_iterations):
        encoded,jac=predict_jacobian(x,m,space);residual=(encoded-target).ravel()
        h=jac.T@jac;g=jac.T@residual+ridge*(m-identity).ravel()
        if np.max(np.abs(g))<1e-10:break
        accepted=False
        for _ in range(8):
            delta=np.linalg.solve(h+(ridge+damping)*np.eye(9),-g).reshape(3,3)
            trial=m+delta
            if valid(trial):
                value=score(trial)
                if value<best-1e-14:
                    improvement=best-value;m=trial;best=value;damping=max(damping/3,1e-10);accepted=True;break
            damping=min(damping*10,1e12)
        if not accepted or improvement<1e-12:break
    return m
