#!/usr/bin/env python3
"""NTV Forge 1.0: LAN-only playlist server; Python standard library."""
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree.ElementTree import Element, SubElement, tostring
import json, uuid, os, socket, tempfile, threading, ipaddress, subprocess, shutil, time, re, urllib.parse, urllib.request, urllib.error, atexit, mimetypes, logging
from channel_forge import app as channel_forge_app

# In a Windows onedir executable, bundled UI assets live under _internal;
# writable project data stays beside the executable.
import sys
HERE=Path(__file__).resolve().parent
ASSETS=Path(getattr(sys, '_MEIPASS', HERE))
if getattr(sys, 'frozen', False):
    HERE=Path(sys.executable).resolve().parent
# Make bundled portable utilities discoverable to child processes (including
# yt-dlp's automatic Deno JavaScript runtime detection). Prefer bundled tools
# over machine-wide installations without changing Windows settings.
TOOLS_DIR = HERE / 'tools'
if TOOLS_DIR.is_dir():
    os.environ['PATH'] = str(TOOLS_DIR) + os.pathsep + os.environ.get('PATH', '')
DATA=HERE/'project.json'
PORT=8765
LOCK=threading.RLock()
def get_project():
    with LOCK:
        try:
            obj=json.loads(DATA.read_text(encoding='utf-8'))
            if isinstance(obj.get('sources'),list) and isinstance(obj.get('channels'),list):
                return obj
        except (FileNotFoundError, ValueError, TypeError, AttributeError): pass
        return {'sources':[],'channels':[]}
def valid_url(u):
    if not isinstance(u,str):return False
    p=urlsplit(u)
    return p.scheme in ('http','https') and bool(p.netloc)
# Resolver URLs remain on the local server; do not put expiring signed URLs in the M3U.
def source_type(url):
    u=urlsplit(url); host=(u.hostname or '').lower(); path=u.path.lower()
    if host in ('youtube.com','www.youtube.com','m.youtube.com','youtu.be','music.youtube.com'):
        if 'list' in urllib.parse.parse_qs(u.query) or path.startswith('/playlist'):return 'playlist'
        return 'youtube'
    if host in ('twitch.tv','www.twitch.tv','m.twitch.tv'):return 'twitch'
    if path.endswith(('.m3u8','.mp4','.ts')):return 'direct'
    return None

def bundled_tool(name):
    """Prefer the portable toolchain included with the Windows distribution."""
    exe = name + ('.exe' if os.name == 'nt' else '')
    tools_dir = HERE / 'tools'
    candidate = tools_dir / exe
    if candidate.is_file():
        return str(candidate)
    if name == 'streamlink':
        portable = tools_dir / 'streamlink-portable'
        # The official Streamlink archive ships a complete embedded runtime.
        # Do not move streamlink.exe out of its original bin folder.
        for candidate in (portable / 'bin' / exe, portable / exe):
            if candidate.is_file():
                return str(candidate)
    return None

def tool(name):
    result = bundled_tool(name) or shutil.which(name)
    if not result:
        raise RuntimeError(f'{name} not found. Run Download Portable Tools.bat or install it on PATH.')
    return result

def command_output(cmd,timeout=90):
    p=subprocess.run(cmd,capture_output=True,text=True,timeout=timeout,errors='replace',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if p.returncode:raise RuntimeError((p.stderr or p.stdout)[-650:])
    return p.stdout.strip()

def resolve(url):
    kind=source_type(url)
    if kind=='direct':return url
    if kind=='twitch':return command_output([tool('streamlink'),'--stream-url',url,'best'],timeout=55).splitlines()[-1]
    if kind=='youtube':
        return command_output([tool('yt-dlp'),'--no-playlist','--extractor-args','youtube:player_client=web_embedded','-g','-f','(best[protocol=m3u8_native]/best[protocol=m3u8]/best[ext=mp4]/best)',url],timeout=100).splitlines()[0]
    raise RuntimeError('Unsupported source')

PLAYLIST_CACHE={}
def playlist_entries(url):
    cached=PLAYLIST_CACHE.get(url)
    if cached and time.monotonic()-cached[0]<1800:return cached[1]
    out=command_output([tool('yt-dlp'),'--flat-playlist','--dump-single-json','--skip-download',url],timeout=180)
    obj=json.loads(out)
    entries=[]
    for e in obj.get('entries',[]):
        if not isinstance(e,dict):continue
        v=e.get('id')
        if isinstance(v,str) and re.fullmatch(r'[A-Za-z0-9_-]{11}',v):entries.append('https://www.youtube.com/watch?v='+v)
    if not entries:raise RuntimeError('No accessible YouTube videos found in playlist')
    PLAYLIST_CACHE[url]=(time.monotonic(),entries)
    return entries

def eligible():
    d=get_project()
    sources={s.get('id'):s for s in d['sources'] if isinstance(s,dict)}
    for c in d['channels']:
        if not isinstance(c,dict):continue
        s=sources.get(c.get('sourceId'))
        if not s:continue
        url=s.get('url','')
        if not valid_url(url):continue
        if source_type(url):
            yield c,url
def clean(s):
    return str(s or '').replace('\r',' ').replace('\n',' ').replace('"',"'")
def m3u(host=None):
    lines=['#EXTM3U']
    for c,u in eligible():
        n=clean(c.get('number'));name=clean(c.get('name'))
        lines.append(f'#EXTINF:-1 tvg-id="lf.{n}" tvg-name="{name}" group-title="Link Forge",{name}')
        if source_type(u)=='direct':lines.append(u)
        else:lines.append(f'http://{host or (lan_ip()+":"+str(PORT))}/hls/{urllib.parse.quote(str(c.get("id")),safe="")}/index.m3u8')
    return ('\n'.join(lines)+'\n').encode('utf-8')
def xmltv():
    root=Element('tv',{'generator-info-name':'Link Forge Lite'})
    for c,_ in eligible():
        number=str(c.get('number',''))
        node=SubElement(root,'channel',{'id':'lf.'+number})
        SubElement(node,'display-name').text=f'{number} {c.get("name","")}'
    return b'<?xml version="1.0" encoding="UTF-8"?>\n'+tostring(root,encoding='utf-8')
def lan_ip():
    try:
        s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
        s.connect(('192.0.2.1',80))
        ip=s.getsockname()[0];s.close()
        return ip
    except OSError:return 'YOUR-PC-LAN-IP'
def lan_ips():
    candidates=[]
    try:
        candidates.append(lan_ip())
        for info in socket.getaddrinfo(socket.gethostname(),None,family=socket.AF_INET):
            candidates.append(info[4][0])
    except OSError:
        pass
    unique=[]
    for ip in candidates:
        try:
            addr=ipaddress.ip_address(ip)
            if addr.is_private and not addr.is_loopback and not addr.is_link_local and ip not in unique:
                unique.append(ip)
        except ValueError:
            continue
    return unique or [lan_ip()]
# HLS sessions run FFmpeg on demand, creating short MPEG-TS segments.
# This is an experimental local-LAN playback bridge, not a public streaming server.
HLS_ROOT=Path(tempfile.mkdtemp(prefix='link-forge-hls-'))
HLS_SESSIONS={}
HLS_LOCK=threading.RLock()
HLS_IDLE=150

def channel_source(cid):
    project=get_project()
    channel=next((c for c in project['channels'] if isinstance(c,dict) and c.get('id')==cid),None)
    return next((s for s in project['sources'] if isinstance(s,dict) and channel and s.get('id')==channel.get('sourceId')),None)

def stop_session(session):
    proc=session.get('proc')
    if proc and proc.poll() is None:
        proc.kill()
        try:proc.wait(timeout=3)
        except subprocess.TimeoutExpired:pass

def hls_worker(session, source_url):
    try:
        entries=playlist_entries(source_url) if source_type(source_url)=='playlist' else [source_url]
        failures=0; idx=0
        while not session['stop'].is_set():
            try:
                resolved=resolve(entries[idx])
                # DXVA2 GPU scaling was validated on Intel UHD 630 with a fixed
                # QSV frame pool. Decoding remains software for source compatibility.
                # Fall back to the proven QSV encode and then x264 on startup failure.
                output=[ '-map','0:v:0?','-map','0:a:0?',
                        '-sc_threshold','0',
                        '-c:a','aac','-b:a','128k','-ar','44100','-ac','2',
                        '-f','hls','-hls_time','4','-hls_list_size','8',
                        '-hls_flags','append_list+delete_segments+omit_endlist+independent_segments',
                        '-hls_segment_filename',str(session['dir']/'seg_%06d.ts'),
                        str(session['dir']/'index.m3u8')]
                # Preserve the source dimensions and frame rate. QSV still encodes
                # to H.264 for HLS compatibility; it is not bit-for-bit direct play.
                # Some sources / GPU drivers may fail at high resolutions; fallback
                # changes encoder, not output dimensions.
                variants=[
                    ('Quick Sync encode (native resolution)', [],
                     ['-c:v','h264_qsv','-global_quality','23','-pix_fmt','nv12']),
                    ('software fallback (native resolution)', [],
                     ['-c:v','libx264','-preset','veryfast','-tune','zerolatency',
                      '-pix_fmt','yuv420p'])]
                for mode,input_opts,video_opts in variants:
                    if session['stop'].is_set():break
                    # Do not carry a partially written manifest into fallback.
                    if mode!=variants[0][0]:
                        for old in session['dir'].glob('seg_*.ts'):
                            try:old.unlink()
                            except OSError:pass
                        try:(session['dir']/'index.m3u8').unlink()
                        except OSError:pass
                    cmd=[tool('ffmpeg'),'-hide_banner','-loglevel','warning','-nostdin','-re']+input_opts+['-i',resolved]+video_opts+output
                    session['mode']=mode
                    print('HLS encoding:',mode,'channel:',session.get('cid','unknown'),flush=True)
                    with open(session['dir']/'ffmpeg.log','ab') as log:
                        log.write(('\n=== '+mode+' ===\n').encode());log.flush()
                        proc=subprocess.Popen(cmd,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=log,
                            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                        session['proc']=proc
                        # Watch startup: if QSV fails to initialize, automatically retry x264.
                        startup=time.monotonic()
                        while proc.poll() is None and not session['stop'].wait(1):
                            if time.monotonic()-session['last']>HLS_IDLE:session['stop'].set()
                        if proc.poll() is None:stop_session(session)
                        produced=any(session['dir'].glob('seg_*.ts'))
                        if mode!='software fallback' and proc.returncode not in (None,0) and not produced and not session['stop'].is_set():
                            print(mode,'failed before HLS output; trying next encoding mode.',flush=True)
                            continue
                        break
                if session['stop'].is_set():break
                failures=0 if (session['dir']/'index.m3u8').exists() else failures+1
                if source_type(source_url)=='twitch' or failures>=min(5,len(entries)):break
                idx=(idx+1)%len(entries)
                # append_list keeps segment numbering continuous across videos.
            except Exception as exc:
                session['error']=str(exc)[-300:]
                print('HLS worker error:',session['error'])
                break
    finally:
        session['done']=True

def ensure_hls(cid):
    source=channel_source(cid)
    if not source or source_type(source.get('url','')) not in ('youtube','twitch','playlist'):
        return None
    with HLS_LOCK:
        session=HLS_SESSIONS.get(cid)
        if session and not session['stop'].is_set() and not session['done']:
            session['last']=time.monotonic()
            return session
        if session:stop_session(session)
        folder=HLS_ROOT/re.sub('[^A-Za-z0-9_-]','_',cid)
        folder.mkdir(parents=True,exist_ok=True)
        for old in folder.glob('seg_*.ts'):
            try:old.unlink()
            except OSError:pass
        try:(folder/'index.m3u8').unlink()
        except OSError:pass
        session={'cid':cid,'dir':folder,'last':time.monotonic(),'stop':threading.Event(),
                 'done':False,'error':None,'proc':None}
        HLS_SESSIONS[cid]=session
        threading.Thread(target=hls_worker,args=(session,source['url']),daemon=True).start()
        return session

@atexit.register
def shutdown_hls():
    for session in list(HLS_SESSIONS.values()):
        session['stop'].set()
        stop_session(session)
    shutil.rmtree(HLS_ROOT,ignore_errors=True)

class Handler(BaseHTTPRequestHandler):
    protocol_version="HTTP/1.1"
    def log_message(self, fmt, *args):
        # stderr may be None in PyInstaller --windowed executables.
        logging.getLogger('ntv.http').info('%s - %s', self.address_string(), fmt % args)
    def handle_one_request(self):
        try:
            return super().handle_one_request()
        except Exception:
            logging.getLogger('ntv.http').exception('HTTP request failed')
            raise
    def reply(self,status,body,typ):
        self.send_response(status)
        self.send_header('Content-Type',typ)
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.end_headers()
        if self.command != "HEAD":self.wfile.write(body)
    def do_HEAD(self):
        return self.do_GET()
    def do_GET(self):
        p=urlsplit(self.path).path
        if p in ('/','/index.html'):
            return self.reply(200,(ASSETS/'suite.html').read_bytes(),'text/html; charset=utf-8')
        if p=='/link':return self.reply(200,(ASSETS/'index.html').read_bytes(),'text/html; charset=utf-8')
        if p=='/forge':return self.reply(200,(ASSETS/'channel_forge'/'index.html').read_bytes(),'text/html; charset=utf-8')
        if p=='/forge/logo.svg':return self.reply(200,(ASSETS/'channel_forge'/'forge-logo.svg').read_bytes(),'image/svg+xml')
        if p=='/forge/api/sample':return self.reply(200,(ASSETS/'channel_forge'/'channels.csv').read_bytes(),'text/csv; charset=utf-8')
        if p=='/channels.m3u':return self.reply(200,m3u(self.headers.get('Host') if self.headers.get('Host','').split(':')[0] in ['localhost','127.0.0.1',*lan_ips()] else None),'audio/x-mpegurl; charset=utf-8')
        if p.startswith('/hls/'):return self.hls(p)
        if p.startswith('/play/'):return self.play(urllib.parse.unquote(p[len('/play/'):]))
        if p=='/api/tools':return self.reply(200,json.dumps({k:bool(bundled_tool(k) or shutil.which(k)) for k in ('yt-dlp','streamlink','ffmpeg')}).encode(),'application/json')
        if p in ('/guide.xml','/epg.xml'):return self.reply(200,xmltv(),'application/xml; charset=utf-8')
        if p=='/favicon.ico':return self.reply(204,b'','image/x-icon')
        if p=='/api/status':return self.source_status(urllib.parse.parse_qs(urlsplit(self.path).query).get('id',[''])[0])
        if p=='/api/project':return self.reply(200,json.dumps(get_project()).encode(),'application/json')
        if p=='/api/network':return self.reply(200,json.dumps({'ip':lan_ip(),'ips':lan_ips()}).encode(),'application/json')
        self.reply(404,b'Not found','text/plain')
    def source_status(self, sid):
        project=get_project()
        source=next((x for x in project['sources'] if isinstance(x,dict) and x.get('id')==sid),None)
        if not source:return self.reply(404,b'Source not found','text/plain')
        url=source.get('url','')
        kind=source_type(url)
        result={'state':'unknown','message':'Could not confirm availability.'}
        try:
            if kind=='twitch':
                command_output([tool('streamlink'),'--stream-url',url,'best'],timeout=18)
                result={'state':'online','message':'Twitch stream is currently available.'}
            elif kind in ('youtube','playlist'):
                args=[tool('yt-dlp'),'--skip-download','--no-playlist','--dump-single-json','--extractor-args','youtube:player_client=web_embedded',url]
                if kind=='playlist':
                    entries=playlist_entries(url)
                    result={'state':'online','message':f'Playlist found ({len(entries)} video links). Individual videos are not verified.'}
                else:
                    metadata=json.loads(command_output(args,timeout=25))
                    live=metadata.get('live_status')
                    if live=='is_live':result={'state':'online','message':'Live broadcast available.'}
                    elif live in ('post_live','was_live'):
                        result={'state':'archived','message':'Broadcast ended; an archived recording may be available.'}
                    elif live=='is_upcoming':result={'state':'offline','message':'Broadcast has not started yet.'}
                    else:result={'state':'online','message':'Video metadata is accessible; playback is not guaranteed.'}
            elif kind=='direct':
                req=urllib.request.Request(url,method='HEAD',headers={'User-Agent':'NTVForge/0.5.0'})
                try:
                    with urllib.request.urlopen(req,timeout=10) as r:code=r.status
                except urllib.error.HTTPError as exc:
                    code=exc.code
                if code in (200,206):result={'state':'online','message':'Server responded; playback is not guaranteed.'}
                elif code in (401,403,405):result={'state':'unknown','message':f'Server returned HTTP {code}; playback may still work.'}
                else:result={'state':'offline','message':f'Server returned HTTP {code}.'}
            else:result={'state':'unknown','message':'This URL format is not supported.'}
        except (RuntimeError,subprocess.TimeoutExpired,urllib.error.URLError,ValueError,IndexError,OSError) as exc:
            result={'state':'unknown','message':'Could not verify source right now. It may be offline, restricted, or temporarily unavailable.'}
        return self.reply(200,json.dumps(result).encode(),'application/json')

    def hls(self,path):
        parts=path.split('/')
        if len(parts)!=4 or parts[3] not in ('index.m3u8',) and not re.fullmatch(r'seg_[0-9]{6}\.ts',parts[3]):
            return self.reply(404,b'Not found','text/plain')
        cid=urllib.parse.unquote(parts[2]); filename=parts[3]
        if self.command=='HEAD':
            src=channel_source(cid)
            if not src or source_type(src.get('url','')) not in ('youtube','twitch','playlist'):
                return self.reply(404,b'Channel not found','text/plain')
            return self.reply(200,b'','application/vnd.apple.mpegurl' if filename.endswith('.m3u8') else 'video/mp2t')
        session=ensure_hls(cid)
        if not session:return self.reply(404,b'Channel not found','text/plain')
        session['last']=time.monotonic()
        file=session['dir']/filename
        if filename=='index.m3u8' and self.command=='GET':
            # Wait for initial segments rather than returning an empty playlist.
            deadline=time.monotonic()+25
            while time.monotonic()<deadline and not file.exists() and not session['done']:
                time.sleep(.2)
        if not file.is_file():
            msg=('HLS not ready: '+(session.get('error') or 'try again shortly')).encode()
            return self.reply(503 if not session['done'] else 502,msg,'text/plain')
        try:body=file.read_bytes()
        except OSError:return self.reply(503,b'Segment is being refreshed','text/plain')
        typ='application/vnd.apple.mpegurl' if filename.endswith('.m3u8') else 'video/mp2t'
        return self.reply(200,body,typ)

    def play(self,cid):
        project=get_project()
        channel=next((c for c in project['channels'] if isinstance(c,dict) and c.get('id')==cid),None)
        source=next((s for s in project['sources'] if isinstance(s,dict) and channel and s.get('id')==channel.get('sourceId')),None)
        if not source:return self.reply(404,b'Channel not found','text/plain')
        url=source.get('url','')
        if source_type(url) not in ('youtube','twitch','playlist'):
            return self.reply(400,b'Use direct URL from M3U for direct sources','text/plain')
        if not (bundled_tool('ffmpeg') or shutil.which('ffmpeg')):return self.reply(503,b'FFmpeg required for Link Forge playback proxy','text/plain')
        if self.command=='HEAD':return self.reply(200,b'','video/mp2t')
        try:
            entries=playlist_entries(url) if source_type(url)=='playlist' else [url]
            # Pre-resolve first entry before sending HTTP 200; errors return meaningful HTTP status.
            current=resolve(entries[0]); index=0
        except Exception as e:
            print('Resolve failed:',str(e)[-400:]);return self.reply(502,('Source unavailable: '+str(e)[-300:]).encode(),'text/plain')
        self.send_response(200)
        self.send_header('Content-Type','video/mp2t')
        self.send_header('Cache-Control','no-store')
        self.send_header('Connection','close')
        self.end_headers()
        self.close_connection=True
        # One ffmpeg process per item; each outputs MPEG-TS for clients that support it.
        # Playlist is sequential, loops after last item, and skips unavailable items.
        failures=0
        while True:
            cmd=[tool('ffmpeg'),'-hide_banner','-loglevel','error','-nostdin','-re','-i',current,
                 '-map','0:v:0?','-map','0:a:0?','-c:v','copy','-c:a','aac','-f','mpegts','pipe:1']
            proc=None; sent=0
            try:
                proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                while True:
                    block=proc.stdout.read(188*64)
                    if not block:break
                    self.wfile.write(block);sent+=len(block)
                failures=0 if sent else failures+1
            except (BrokenPipeError,ConnectionResetError,OSError):break
            finally:
                if proc:
                    if proc.poll() is None:proc.kill()
                    proc.wait()
                    if proc.stdout:proc.stdout.close()
            if source_type(url)!='playlist':break
            if failures>=min(5,len(entries)):break
            index=(index+1)%len(entries)
            for _ in range(len(entries)):
                try:
                    current=resolve(entries[index]);break
                except Exception as e:
                    print('Skipping playlist item:',str(e)[-250:]);failures+=1
                    index=(index+1)%len(entries)
            else:break

    def send(self,status,data,content_type='application/json',filename=None):
        body=data.encode('utf-8') if isinstance(data,str) else data
        self.send_response(status)
        self.send_header('Content-Type',content_type+'; charset=utf-8')
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        if filename:self.send_header('Content-Disposition','attachment; filename="'+filename+'"')
        self.end_headers()
        self.wfile.write(body)
    def do_POST(self):
        path=urlsplit(self.path).path
        if path not in ('/forge/api/scan','/forge/api/test-connection','/forge/api/parse','/forge/api/export'):
            return self.reply(404,b'Not found','text/plain')
        self.path=path[len('/forge/api'):]
        try:channel_forge_app.Handler.do_POST(self)
        finally:self.path=path

    def do_PUT(self):
        if urlsplit(self.path).path!='/api/project':return self.reply(404,b'Not found','text/plain')
        try:
            size=int(self.headers.get('Content-Length','0'))
            if size<1 or size>2_000_000:raise ValueError('Project too large or empty')
            obj=json.loads(self.rfile.read(size))
            if not isinstance(obj,dict) or not isinstance(obj.get('sources'),list) or not isinstance(obj.get('channels'),list):raise ValueError('Invalid project')
            if len(obj['sources'])>5000 or len(obj['channels'])>5000:raise ValueError('Too many entries')
            payload=json.dumps({'sources':obj['sources'],'channels':obj['channels']},ensure_ascii=False,indent=2)
            with LOCK:
                fd,tmp=tempfile.mkstemp(prefix='.linkforge-',dir=HERE)
                try:
                    with os.fdopen(fd,'w',encoding='utf-8') as f:f.write(payload)
                    os.replace(tmp,DATA)
                finally:
                    if os.path.exists(tmp):os.unlink(tmp)
            return self.reply(200,b'{"ok":true}','application/json')
        except (ValueError,TypeError,UnicodeDecodeError) as e:
            return self.reply(400,str(e).encode(),'text/plain')
if __name__=='__main__':
    print(f'NTV Forge 1.0 running: http://localhost:{PORT}')
    print(f'LAN playlist: http://{lan_ip()}:{PORT}/channels.m3u')
    print('Only use on trusted networks. Press Ctrl+C to stop.')
    ThreadingHTTPServer(('0.0.0.0',PORT),Handler).serve_forever()
