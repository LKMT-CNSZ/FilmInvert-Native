"""Original synthetic negative/mask-removal/positive demonstration assets."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'vendor')]
import numpy as np,core
from PIL import Image,ImageDraw
im=Image.new('RGB',(600,420),'#9baeb5');d=ImageDraw.Draw(im)
d.rectangle((0,230,600,420),fill='#8b9778');d.rectangle((75,100,470,280),fill='#d6cbbb')
for x in range(105,455,63):d.rectangle((x,136,x+31,216),fill='#46575a')
d.rectangle((250,213,302,283),fill='#675844');d.rectangle((30,330,570,377),fill='#9b8981')
for i,color in enumerate(['#c65342','#cf9b55','#dad1b3','#80a0ad','#778b68','#565657']):d.rectangle((36+i*89,338,112+i*89,369),fill=color)
linear=core.srgb_decode(np.asarray(im,np.float32)/255);base=np.array([.72,.48,.29],np.float32)
negative=base*np.power(10.,-.75*np.clip(core.srgb_encode(linear),0,1))
positive=core.process(negative,core.Params(base=base.tolist(),span=.75,slope=1.))
assets=ROOT/'tools/installer-assets';assets.mkdir(exist_ok=True)
for name,arr in [('negative',core.srgb_encode(negative)),('mask',core.srgb_encode(negative/base)),('positive',positive)]:
    Image.fromarray(np.rint(np.clip(arr,0,1)*255).astype('uint8')).resize((200,148)).save(assets/(name+'.bmp'))
print('Prepared three original synthetic installer demo stages')
