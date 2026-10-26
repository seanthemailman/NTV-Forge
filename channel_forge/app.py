#!/usr/bin/env python3
"""NTV Channel Forge: local-only media metadata scanner and CSV lineup editor."""
import csv, io, json, os, re, threading, urllib.parse, urllib.request, urllib.error, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT=Path(__file__).resolve().parent
HEADERS=['Channel Number','Channel Name','Title','Release Year']
MAX_BODY=12*1024*1024

def get_json(url, headers=None):
    req=urllib.request.Request(url,headers=headers or {'Accept':'application/json'})
    with urllib.request.urlopen(req,timeout=35) as res:
        if res.status!=200: raise ValueError('Media server returned HTTP '+str(res.status))
        return json.load(res)

def normalize(items):
    out=[]; seen=set()
    for m in items:
        title=str(m.get('title') or '').strip(); year=m.get('year')
        if not title: continue
        try: year=int(year) if year else ''
        except (TypeError,ValueError): year=''
        typ='TV' if str(m.get('type','')).lower() in ('show','series','tv') else 'Movie'
        genres=sorted(set(str(x).strip() for x in (m.get('genres') or []) if str(x).strip()))
        key=(title.casefold(),year,typ)
        if key in seen: continue
        seen.add(key);out.append({'title':title,'year':year,'type':typ,'genres':genres})
    return sorted(out,key=lambda x:(x['title'].casefold(),str(x['year'])))

def scan_plex(base,token):
    h={'X-Plex-Token':token,'Accept':'application/json'}
    base=base.rstrip('/')
    sections=get_json(base+'/library/sections',h).get('MediaContainer',{}).get('Directory',[])
    found=[]
    for sec in sections:
        if sec.get('type') not in ('movie','show'):continue
        start=0
        while True:
            u=base+'/library/sections/'+urllib.parse.quote(str(sec['key']),safe='')+'/all?'+urllib.parse.urlencode({'X-Plex-Container-Start':start,'X-Plex-Container-Size':500})
            data=get_json(u,h).get('MediaContainer',{})
            arr=data.get('Metadata',[])
            for x in arr:
                found.append({'title':x.get('title'),'year':x.get('year'),'type':sec['type'],'genres':[g.get('tag','') for g in x.get('Genre',[])]})
            start+=len(arr)
            if not arr or start>=int(data.get('totalSize',start)):break
    return normalize(found)

def jellyfin_headers(token):
    if not token or any(c in token for c in ('"', '\r', '\n')):
        raise ValueError('Invalid API key characters')
    return {'Authorization': 'MediaBrowser Token="'+token+'"', 'Accept':'application/json'}

def test_plex(base, token):
    h={'X-Plex-Token':token,'Accept':'application/json'}
    checks=[]
    for endpoint in ('/identity','/library/sections'):
        try:
            result=get_json(base.rstrip('/')+endpoint,h)
            container=result.get('MediaContainer',{})
            detail=('Libraries: '+str(len(container.get('Directory',[])))) if endpoint=='/library/sections' else 'OK'
            checks.append({'endpoint':endpoint,'status':200,'detail':detail})
        except urllib.error.HTTPError as e:
            checks.append({'endpoint':endpoint,'status':e.code,'detail':'HTTP '+str(e.code)})
        except Exception as e:
            checks.append({'endpoint':endpoint,'status':None,'detail':str(e)})
    return checks

def test_jellyfin(base,token):
    h=jellyfin_headers(token)
    checks=[]
    for endpoint in ('/System/Info','/Users'):
        try:
            result=get_json(base.rstrip('/')+endpoint,h)
            checks.append({'endpoint':endpoint,'status':200,'detail':('Users: '+str(len(result))) if endpoint=='/Users' and isinstance(result,list) else 'OK'})
        except urllib.error.HTTPError as e:
            checks.append({'endpoint':endpoint,'status':e.code,'detail':'HTTP '+str(e.code)})
        except Exception as e:
            checks.append({'endpoint':endpoint,'status':None,'detail':str(e)})
    return checks

def scan_jellyfin(base,token):
    base=base.rstrip('/')
    # Use the exact minimal MediaBrowser Token scheme confirmed against Jellyfin 12.1.
    # Do not send the older X-Emby-* headers.
    h=jellyfin_headers(token)
    try:
        users=get_json(base+'/Users',h)
    except urllib.error.HTTPError as e:
        if e.code in (401,403):
            raise ValueError('Jellyfin rejected the API key while requesting /Users (HTTP '+str(e.code)+'). The modern MediaBrowser Authorization header was sent. Verify the API key in Dashboard > API Keys and the server URL.') from e
        raise
    if not users:raise ValueError('No accessible users returned by server')
    # Try accessible users rather than assuming the first user can see all libraries.
    found=[]; errors=[]
    for user in users:
        uid=urllib.parse.quote(str(user['Id']),safe='');start=0
        try:
            while True:
                qs=urllib.parse.urlencode({'Recursive':'true','IncludeItemTypes':'Movie,Series','Fields':'Genres,ProductionYear','StartIndex':start,'Limit':500})
                data=get_json(base+'/Users/'+uid+'/Items?'+qs,h)
                arr=data.get('Items',[])
                for x in arr:found.append({'title':x.get('Name'),'year':x.get('ProductionYear'),'type':x.get('Type'),'genres':x.get('Genres',[])})
                start+=len(arr)
                if not arr or start>=int(data.get('TotalRecordCount',start)):break
        except urllib.error.HTTPError as e:
            if e.code in (401,403):errors.append(str(user.get('Name','user'))+' (HTTP '+str(e.code)+')')
            else:raise
    if not found and errors:
        raise ValueError('API key was accepted for /Users, but library access was denied for: '+', '.join(errors)+'. Check user library permissions.')
    return normalize(found)

def parse_csv(raw):
    r=csv.DictReader(io.StringIO(raw.lstrip('\ufeff')))
    if r.fieldnames!=HEADERS:raise ValueError('CSV columns must be: '+', '.join(HEADERS))
    result=[]
    for row in r:
        if not row.get('Channel Number') or not row.get('Channel Name') or not row.get('Title'):continue
        result.append({k:str(row.get(k) or '').strip() for k in HEADERS})
    return result

def make_csv(rows):
    f=io.StringIO(newline='');w=csv.DictWriter(f,fieldnames=HEADERS,lineterminator='\r\n');w.writeheader()
    for row in rows:w.writerow({k:row.get(k,'') for k in HEADERS})
    return f.getvalue()

class Handler(BaseHTTPRequestHandler):
    def send(self,status,data,content_type='application/json',filename=None):
        b=data.encode('utf-8') if isinstance(data,str) else data
        self.send_response(status);self.send_header('Content-Type',content_type+'; charset=utf-8');self.send_header('Content-Length',str(len(b)));self.send_header('Cache-Control','no-store')
        if filename:self.send_header('Content-Disposition','attachment; filename="'+filename+'"')
        self.end_headers();self.wfile.write(b)
    def do_GET(self):
        if self.path=='/':self.send(200,(ROOT/'index.html').read_bytes(),'text/html');return
        if self.path=='/sample':self.send(200,(ROOT/'channels.csv').read_bytes(),'text/csv');return
        self.send(404,json.dumps({'error':'Not found'}))
    def do_POST(self):
        try:
            length=int(self.headers.get('Content-Length','0'))
            if length>MAX_BODY:raise ValueError('Request too large')
            data=json.loads(self.rfile.read(length))
            if self.path in ('/scan','/test-connection'):
                base=str(data.get('url','')).strip();parsed=urllib.parse.urlparse(base)
                if parsed.scheme not in ('http','https') or not parsed.hostname:raise ValueError('Enter a valid http(s) media-server URL')
                token=str(data.get('token','')).strip()
                if not token:raise ValueError('An API token is required')
                kind=data.get('kind')
                if self.path=='/test-connection':
                    if kind=='plex':checks=test_plex(base,token)
                    elif kind in ('jellyfin','emby'):checks=test_jellyfin(base,token)
                    else:raise ValueError('Unknown server type')
                    self.send(200,json.dumps({'checks':checks}));return
                if kind=='plex':items=scan_plex(base,token)
                elif kind in ('jellyfin','emby'):items=scan_jellyfin(base,token)
                else:raise ValueError('Unknown server type')
                self.send(200,json.dumps({'items':items}));return
            if self.path=='/parse':self.send(200,json.dumps({'rows':parse_csv(str(data.get('csv','')))}));return
            if self.path=='/export':
                rows=data.get('rows',[])
                if not isinstance(rows,list) or len(rows)>100000:raise ValueError('Invalid row count')
                for r in rows:
                    if not all(str(r.get(k,'')).strip() for k in ('Channel Number','Channel Name','Title')):raise ValueError('Every row needs channel number, name and title')
                    if not str(r['Channel Number']).isdigit():raise ValueError('Channel numbers must be numeric')
                self.send(200,make_csv(rows),'text/csv', 'channels.csv' if data.get('mode')=='builtin' else 'custom_collection.csv');return
            raise ValueError('Unknown endpoint')
        except Exception as e:self.send(400,json.dumps({'error':str(e)}))
    def log_message(self,*args):pass

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=8765);args=p.parse_args()
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    print(f'Channel Forge: http://127.0.0.1:{args.port} (Ctrl+C to stop)',flush=True)
    webbrowser.open(f'http://127.0.0.1:{args.port}')
    server.serve_forever()
