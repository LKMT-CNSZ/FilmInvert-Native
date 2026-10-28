"""Nominal sRGB chart diagnostics. CIEDE2000 kL=kC=kH=1; Sharma/Wu/Dalal (2005)."""
import numpy as np
D50=np.array([.9642,1.,.8249]);D65=np.array([.95047,1.,1.08883])
B=np.array([[.8951,.2664,-.1614],[-.7502,1.7135,.0367],[.0389,-.0685,1.0296]])
ADAPT=np.linalg.solve(B,np.diag((B@D50)/(B@D65))@B)
M=np.array([[.4124564,.3575761,.1804375],[.2126729,.7151522,.0721750],[.0193339,.1191920,.9503041]])
def lab_xyz(lab):
 lab=np.asarray(lab,float);fy=(lab[...,0]+16)/116;f=np.stack([fy+lab[...,1]/500,fy,fy-lab[...,2]/200],axis=-1)
 return np.where(f>6/29,f**3,3*(6/29)**2*(f-4/29))*D50

def xyz_lab(xyz):
 r=np.asarray(xyz,float)/D50;f=np.where(r>(6/29)**3,np.cbrt(r),r/(3*(6/29)**2)+4/29)
 return np.stack([116*f[...,1]-16,500*(f[...,0]-f[...,1]),200*(f[...,1]-f[...,2])],axis=-1)

def linear_from_lab(lab):return lab_xyz(lab)@np.linalg.inv(ADAPT).T@np.linalg.inv(M).T

def lab_from_linear(rgb):return xyz_lab(np.asarray(rgb,float)@M.T@ADAPT.T)

def de00(a,b):
 a,b=np.asarray(a,float),np.asarray(b,float);l1,a1,b1=np.moveaxis(a,-1,0);l2,a2,b2=np.moveaxis(b,-1,0)
 c1=np.hypot(a1,b1);c2=np.hypot(a2,b2);cbar=(c1+c2)/2;g=.5*(1-np.sqrt(cbar**7/(cbar**7+25**7)))
 ap1=(1+g)*a1;ap2=(1+g)*a2;cp1=np.hypot(ap1,b1);cp2=np.hypot(ap2,b2)
 hp1=np.degrees(np.arctan2(b1,ap1))%360;hp2=np.degrees(np.arctan2(b2,ap2))%360
 dl=l2-l1;dc=cp2-cp1;dh=hp2-hp1;zero=cp1*cp2==0
 dh=np.where(zero,0,np.where(dh>180,dh-360,np.where(dh< -180,dh+360,dh)))
 dH=2*np.sqrt(cp1*cp2)*np.sin(np.radians(dh/2));lb=(l1+l2)/2;cb=(cp1+cp2)/2;hs=hp1+hp2
 hb=np.where(zero,hs,np.where(np.abs(hp1-hp2)<=180,hs/2,np.where(hs<360,(hs+360)/2,(hs-360)/2)))
 t=1-.17*np.cos(np.radians(hb-30))+.24*np.cos(np.radians(2*hb))+.32*np.cos(np.radians(3*hb+6))-.20*np.cos(np.radians(4*hb-63))
 sl=1+.015*(lb-50)**2/np.sqrt(20+(lb-50)**2);sc=1+.045*cb;sh=1+.015*cb*t
 rt=-2*np.sqrt(cb**7/(cb**7+25**7))*np.sin(np.radians(60*np.exp(-((hb-275)/25)**2)))
 return np.sqrt((dl/sl)**2+(dc/sc)**2+(dH/sh)**2+rt*(dc/sc)*(dH/sh))

def summary(v):
 v=np.asarray(v,float);return dict(median=float(np.median(v)),mean=float(np.mean(v)),p95=float(np.percentile(v,95)),max=float(np.max(v)),count=int(v.size))


def lab_from_encoded(rgb):
 x=np.asarray(rgb,float);linear=np.where(x<=.04045,x/12.92,((x+.055)/1.055)**2.4)
 return lab_from_linear(linear)

