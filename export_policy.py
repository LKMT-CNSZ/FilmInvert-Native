"""Portable, deterministic export names. User templates cannot create directories."""
from pathlib import Path
import re,string
DEFAULT='{source}-f{frame:04d}-positive'
FIELDS={'source','frame','roll','date'}

def validate(template):
    if not isinstance(template,str) or len(template)>160:raise ValueError('命名模板须为不超过 160 字符的文字')
    if not template:return template
    try:
        for literal,key,spec,conversion in string.Formatter().parse(template):
            if any(c in literal for c in '<>:"/\\|?*') or any(ord(c)<32 for c in literal):raise ValueError('命名模板不能含路径或文件名保留字符')
            if key is None:continue
            if key not in FIELDS or conversion:raise ValueError('模板仅支持 {source}、{frame:04d}、{roll}、{date}')
            if (key=='frame' and not re.fullmatch(r'(?:0?[1-8])?d|',spec)) or (key!='frame' and spec):raise ValueError('模板格式无效；帧号可用 :04d')
    except (ValueError,KeyError) as e:raise ValueError('命名模板无效：'+str(e))
    return template

def clean(value):
    return ''.join('_' if c in '<>:"/\\|?*' or ord(c)<32 else c for c in str(value))[:60].strip(' .') or 'untitled'

def options(template='',roll='',date=''):
    from datetime import datetime
    validate(template)
    date=date or datetime.now().strftime('%Y%m%d')
    if not re.fullmatch(r'\d{8}',date):raise ValueError('命名日期应为 YYYYMMDD')
    return {'template':template,'roll':clean(roll) if roll else '未命名卷','date':date}

def filename(source,frame,config=None,legacy=None):
    config=config or {};template=validate(config.get('template',''))
    if not template:return legacy or f'frame-{frame:04d}.tif'
    name=template.format(source=clean(Path(source).stem),frame=frame,roll=clean(config.get('roll','未命名卷')),date=options(date=config.get('date',''))['date'])
    if len(name)>180 or not name or name!=name.strip(' .') or any(c in name for c in '<>:"/\\|?*') or any(ord(c)<32 for c in name):raise ValueError('命名结果过长、为空或含无效字符')
    if name.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*[f'{prefix}{i}' for prefix in ('COM','LPT') for i in range(1,10)]}:raise ValueError('命名结果是 Windows 保留设备名')
    return name+'.tif'

def names(source,indices,config=None,legacy=None):
    result=[filename(source,i+1,config,legacy(i) if legacy else None) for i in indices]
    if len({n.casefold() for n in result})!=len(result):raise ValueError('模板会产生重复文件名，请加入 {frame:04d}')
    return result
