"""Render the trusted release guide as standalone, readable UTF-8 HTML."""
from pathlib import Path
import html,re
import argparse
ROOT=Path(__file__).resolve().parents[1]
def inline(text):
    text=html.escape(text)
    text=re.sub(r'`([^`]+)`',r'<code>\1</code>',text)
    text=re.sub(r'\*\*([^*]+)\*\*',r'<strong>\1</strong>',text)
    text=re.sub(r'\[([^\]]+)\]\(([^)]+)\)',r'<a href="\2">\1</a>',text)
    return text

def render(source='软件介绍.md',output='软件介绍.html',language='zh-CN'):
    lines=(ROOT/source).read_text('utf-8-sig').splitlines();parts=[];toc=[];i=0
    while i<len(lines):
        line=lines[i]
        if not line.strip():i+=1;continue
        match=re.match(r'^(#{1,3}) (.+)',line)
        if match:
            n=len(match[1]);label=match[2];anchor='section-'+str(i)
            if n==2:toc.append((anchor,label))
            parts.append(f'<h{n} id="{anchor}">{inline(label)}</h{n}>');i+=1;continue
        if line.startswith('|'):
            rows=[]
            while i<len(lines) and lines[i].startswith('|'):
                cells=[c.strip() for c in lines[i].strip('|').split('|')]
                if not all(re.fullmatch(r'[:\- ]+',c) for c in cells):rows.append(cells)
                i+=1
            parts.append('<div class="table-wrap"><table>')
            for j,row in enumerate(rows):
                tag='th' if j==0 else 'td';parts.append('<tr>'+''.join(f'<{tag}>{inline(c)}</{tag}>' for c in row)+'</tr>')
            parts.append('</table></div>');continue
        if re.match(r'^\d+\. ',line) or line.startswith('- '):
            ordered=bool(re.match(r'^\d+\. ',line));tag='ol' if ordered else 'ul';parts.append('<'+tag+'>')
            while i<len(lines) and (bool(re.match(r'^\d+\. ',lines[i])) if ordered else lines[i].startswith('- ')):
                text=re.sub(r'^\d+\. |^- ','',lines[i]);parts.append('<li>'+inline(text)+'</li>');i+=1
            parts.append('</'+tag+'>');continue
        paragraph=[]
        while i<len(lines) and lines[i].strip() and not lines[i].startswith(('#','|','- ')) and not re.match(r'^\d+\. ',lines[i]):
            paragraph.append(lines[i]);i+=1
        parts.append('<p>'+inline(' '.join(paragraph))+'</p>')
    links=''.join(f'<a href="#{a}">{inline(t)}</a>' for a,t in toc)
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>FilmInvert Native 3.0.0 软件介绍</title><style>
    :root{color-scheme:light}*{box-sizing:border-box}body{margin:0;background:#f3f2ee;color:#252525;font:17px/1.85 "Microsoft YaHei","Noto Sans CJK SC",sans-serif}main{max-width:1040px;margin:auto;padding:48px 52px 90px;background:#fff}h1{font-size:34px;line-height:1.4;letter-spacing:-.5px}h2{margin:52px 0 18px;border-top:1px solid #ddd;padding-top:24px;font-size:25px;line-height:1.5}h3{font-size:20px;margin-top:30px}p{margin:16px 0}a{color:#24577c;text-decoration:none}a:hover{text-decoration:underline}nav{padding:20px 24px;background:#f4f4f2;border-left:3px solid #4b4b4b}nav a{display:block;font-size:15px;line-height:1.9}.table-wrap{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:15px}th,td{border:1px solid #d9d9d5;text-align:left;vertical-align:top;padding:10px 14px;min-width:100px}th{background:#f0f0ed}code{background:#eee;padding:2px 5px;word-break:break-word}li{margin:9px 0}footer{border-top:1px solid #ddd;margin-top:50px;padding-top:20px;font-size:14px;color:#666}@media(max-width:700px){main{padding:25px 20px}h1{font-size:27px}body{font-size:16px}}@media print{body{background:white;font-size:11pt}main{padding:0;max-width:none}h2{break-after:avoid}table{font-size:9pt}nav{display:none}a{color:inherit}}
    </style><main>'''+parts[0]+'<nav aria-label="章节导航">'+links+'</nav>'+''.join(parts[1:])+'''<footer>FilmInvert Native 3.0.0 · Windows x64 · 2026-10-28。离线文档；详细发行证据见同目录 RELEASE-VALIDATION-3.0.0.md。</footer></main></html>'''
    version=(ROOT/'VERSION.txt').read_text('utf-8').strip()
    page=page.replace('FilmInvert Native 3.0.0 软件介绍','FilmInvert Native '+version+' 软件介绍')
    page=page.replace('FilmInvert Native 3.0.0 · Windows x64','FilmInvert Native '+version+' · Windows x64')
    page=page.replace('离线文档；详细发行证据见同目录 RELEASE-VALIDATION-3.0.0.md。','离线文档；本次验证见同目录 RELEASE-VALIDATION-'+version+'.md。')
    if language=='en':
        page=page.replace('lang="zh-CN"','lang="en"').replace('软件介绍</title>',' — Introduction</title>').replace('章节导航','Contents')
        page=re.sub(r'<footer>.*?</footer>','<footer>FilmInvert Native '+version+' · Windows x64 · Offline guide</footer>',page,flags=re.S)
    (ROOT/output).write_text(page,encoding='utf-8')
    print('Rendered guide:',len(page),'characters,',len(toc),'sections')
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source',default='软件介绍.md');parser.add_argument('--output',default='软件介绍.html');parser.add_argument('--language',default='zh-CN');args=parser.parse_args()
    render(args.source,args.output,args.language)
