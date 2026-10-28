"""Paginated, shareable sRGB PNG contact sheets of the current edited roll."""
from pathlib import Path
import json,os,shutil,tempfile,uuid
import numpy as np
from PIL import Image,ImageDraw,ImageFont

def export(folder,snapshot,cancel=None,progress=lambda *a:None):
    import desktop_model as model,colorio,core,studio_worker
    parent=Path(folder).resolve();parent.mkdir(parents=True,exist_ok=True)
    final=parent/('联系表-'+uuid.uuid4().hex[:12]);stage=Path(tempfile.mkdtemp(dir=parent,prefix='.contact-'))
    try:
        fonts=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'
        font=next((ImageFont.truetype(str(fonts/name),22) for name in ('msyh.ttc','segoeui.ttf') if (fonts/name).is_file()),ImageFont.load_default())
        from export_policy import names
        indices=list(range(snapshot['frame_count']));source=snapshot['source']['path'];stem=Path(source).stem
        filenames=names(source,indices,snapshot.get('_export'),lambda i:f'{stem}-f{i+1:02d}-positive.tif' if len(indices)>1 else f'{stem}-positive.tif')
        records=[];page=None
        for i in indices:
            if cancel is not None and cancel.is_set():raise ValueError('已取消联系表；未发布输出')
            if i%20==0:
                page=Image.new('RGB',(1600,1900),'white');draw=ImageDraw.Draw(page)
                draw.text((30,18),'FilmInvert · sRGB · '+Path(snapshot['source']['path']).name[:70],font=font,fill='black')
            array,p=studio_worker.frame_parameters(snapshot,i)
            out=core.process(model.engine.downscale(array,600),p,'srgb')
            image=Image.fromarray((np.clip(out,0,1)*255+.5).astype(np.uint8));image.thumbnail((370,300),Image.Resampling.LANCZOS)
            col,row=i%4,(i%20)//4;x,y=20+col*395,70+row*360
            page.paste(image,(x+(370-image.width)//2,y+(300-image.height)//2))
            caption=f'{i+1:04d} · {filenames[i]}'
            while len(caption)>1 and draw.textlength(caption,font=font)>375:caption=caption[:-2]+'…'
            draw.text((x,y+308),caption,font=font,fill='black')
            records.append({'frame':i+1,'page':i//20+1,'cell':i%20,'filename':filenames[i],'params':p.__dict__,'source_region':model.source_region(i)})
            if i%20==19 or i==indices[-1]:page.save(stage/f'contact-{i//20+1:03d}.png',icc_profile=colorio.icc_profile('srgb'))
            progress(i+1,len(indices),'联系表')
        model.write_json(stage/'contact-index.json',{'format':'film-invert-contact','version':1,'encoding':'srgb','source':snapshot['source'],'input':snapshot['input'],'export_options':snapshot.get('_export'),'frames':records})
        if cancel is not None and cancel.is_set():raise ValueError('已取消联系表；未发布输出')
        stage.rename(final)
    except Exception:
        if stage.resolve().parent!=parent or not stage.name.startswith('.contact-'):raise
        shutil.rmtree(stage);raise
    return {'folder':str(final),'pages':(len(records)+19)//20,'frames':len(records)}
