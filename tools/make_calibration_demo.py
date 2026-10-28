"""Create synthetic chart fixtures with known RGB mixing; no manufacturer data."""
from pathlib import Path
import csv
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
import core
from tiffio import write_tiff_rgb16

def make_demo(folder):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    # Explicitly synthetic colours, not ColorChecker reference values.
    colors=np.array([[190,72,65],[72,179,92],[75,104,196],[200,168,65],[167,76,177],[62,169,179],
                     [176,119,74],[104,154,194],[182,94,126],[109,184,128],[142,119,185],[202,129,73],
                     [92,112,78],[146,177,100],[90,159,148],[173,137,158],[124,93,69],[86,126,171],
                     [215,215,215],[184,184,184],[155,155,155],[125,125,125],[98,98,98],[72,72,72]],np.uint8)
    target=core.srgb_decode(colors.astype(np.float32)/255)
    mixing=np.array([[.86,.05,.03],[.04,.89,.02],[.02,.07,.83]],np.float32)
    measured=target@mixing.T
    cell=100;margin=24;image=np.zeros((4*cell+margin*2,6*cell+margin*2,3),np.float32)
    image[:12]=1.  # Known full black and white borders stabilize auto levels.
    for i,color in enumerate(measured):
        r,c=divmod(i,6);image[margin+r*cell:margin+(r+1)*cell,margin+c*cell:margin+(c+1)*cell]=color
    write_tiff_rgb16(str(folder/'calibration-demo.tif'),image)
    with (folder/'calibration-demo-reference.csv').open('w',encoding='utf-8-sig',newline='') as file:
        writer=csv.writer(file);writer.writerow(['name','r','g','b'])
        for i,color in enumerate(colors):writer.writerow([f'DEMO-{i+1:02d}',*map(int,color)])
    return {'image':image,'colors':colors,'mixing':mixing,
            'points':[[(margin+(c+.5)*cell)/image.shape[1],(margin+(r+.5)*cell)/image.shape[0]] for r in range(4) for c in range(6)],
            'box':[margin/image.shape[1],margin/image.shape[0],1-margin/image.shape[1],1-margin/image.shape[0]]}

if __name__=='__main__':
    print('Synthetic calibration demo:',ROOT/'samples');make_demo(ROOT/'samples')
