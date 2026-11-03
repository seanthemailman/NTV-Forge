#!/usr/bin/env python3
"""NTV Forge 1.0: LAN-only playlist server; Python standard library."""
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree.ElementTree import Element, SubElement, tostring
import random, json, uuid, os, socket, tempfile, threading, ipaddress, subprocess, shutil, time, re, urllib.parse, urllib.request, urllib.error, atexit, mimetypes, logging
from channel_forge import app as channel_forge_app
import archive_forge

# In a Windows onedir executable, bundled UI assets live under _internal;
# writable project data stays beside the executable.
import sys
import zipfile
from datetime import datetime
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
VERSION='1.3.2c-header-stability'
DIAG_LOG=HERE/'forge-diagnostics.log'
ENCODER_PREF=HERE/'encoder-preference.json'
def encoder_preference():
    try:
        mode=json.loads(ENCODER_PREF.read_text(encoding='utf-8')).get('mode','auto')
        return mode if mode in ('auto','qsv','software') else 'auto'
    except (OSError,ValueError,AttributeError):return 'auto'

def read_progress(path):
    try:
        content=path.read_text(encoding='utf-8',errors='replace')
        block=content.split('progress=')[-2] if 'progress=' in content else content
        stats={}
        for line in block.splitlines():
            if '=' in line:
                k,v=line.split('=',1)
                if k in ('frame','fps','speed','drop_frames','dup_frames','out_time_ms','out_time_us'):stats[k]=v[:40]
        return stats
    except OSError:return {}


def diagnostic(event, **fields):
    # Never log signed source URLs, credentials, cookies, or complete commands.
    safe={k: re.sub(r'https?://[^\s\]\[\"\']+', '[REDACTED_URL]',str(v)).replace('\n',' ')[:600] for k,v in fields.items()}
    line=json.dumps({'time':datetime.now().isoformat(timespec='seconds'),'event':event,**safe},ensure_ascii=False)
    with LOCK:
        with DIAG_LOG.open('a',encoding='utf-8') as out:out.write(line+'\n')

QUALITY_CHOICES={'auto', '480', '720', '1080', '1440', '2160'}
def source_quality(source):
    value=str((source or {}).get('quality','auto'))
    return value if value in QUALITY_CHOICES else 'auto'

def quality_filter(source):
    quality=source_quality(source)
    if quality=='auto':
        return []
    # Never upscale; cap height, preserving aspect ratio and even dimensions.
    # Source retrieval quality/bandwidth is unchanged in this first test build.
    return ['-vf', f'scale=-2:trunc(min(ih\\,{quality})/2)*2']

LOCK=threading.RLock()
def get_project():
    with LOCK:
        try:
            obj=json.loads(DATA.read_text(encoding='utf-8'))
            if isinstance(obj.get('sources'),list) and isinstance(obj.get('channels'),list):
                return obj
        except (FileNotFoundError, ValueError, TypeError, AttributeError): pass
        return {'sources':[],'channels':[],'achievements':{}}
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

# Shared YouTube verification cooldown. Never retry a challenge across channels.
YOUTUBE_COOLDOWN_SECONDS=30*60
YOUTUBE_BLOCKED_UNTIL=0.0
YOUTUBE_GUARD_LOCK=threading.RLock()

def youtube_guard():
    with YOUTUBE_GUARD_LOCK:
        remaining=max(0,int(YOUTUBE_BLOCKED_UNTIL-time.monotonic()))
    if remaining:
        raise RuntimeError(f'YouTube verification required; requests paused for {remaining//60+1} more minute(s).')

def youtube_challenge(message):
    msg=message.lower()
    return ('confirm you' in msg and 'not a bot' in msg) or ('sign in' in msg and 'not a bot' in msg)

def command_output(cmd,timeout=90):
    is_youtube=Path(cmd[0]).name.lower().startswith('yt-dlp')
    if is_youtube:youtube_guard()
    p=subprocess.run(cmd,capture_output=True,text=True,timeout=timeout,errors='replace',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if p.returncode:
        if is_youtube and youtube_challenge((p.stderr or '')+' '+(p.stdout or '')):
            global YOUTUBE_BLOCKED_UNTIL
            with YOUTUBE_GUARD_LOCK:
                if time.monotonic()>=YOUTUBE_BLOCKED_UNTIL:
                    YOUTUBE_BLOCKED_UNTIL=time.monotonic()+YOUTUBE_COOLDOWN_SECONDS
                    diagnostic('youtube_verification_required',cooldown_minutes=30)
            raise RuntimeError('YouTube verification required; requests paused for 30 minutes.')
        diagnostic('resolver_failed',tool=Path(cmd[0]).name,returncode=p.returncode,stderr=(p.stderr or '')[-600:])
        raise RuntimeError((p.stderr or p.stdout)[-650:])
    return p.stdout.strip()

# Resolve URLs are signed and short-lived. Cache conservatively; never cache
# beyond an explicit expire= value from YouTube's signed URL.
RESOLVE_CACHE={}
RESOLVE_LOCK=threading.RLock()
RESOLVE_INFLIGHT={}
RESOLVE_TTL=240

def resolve(url):
    kind=source_type(url)
    if kind=='direct':return url
    if kind=='twitch':return command_output([tool('streamlink'),'--stream-url',url,'best'],timeout=55).splitlines()[-1]
    if kind!='youtube':raise RuntimeError('Unsupported source')
    # Single-flight: concurrent channels requesting the same video share one
    # yt-dlp request, avoiding bursts when NTV refreshes multiple channels.
    with RESOLVE_LOCK:
        entry=RESOLVE_CACHE.get(url)
        if entry and time.monotonic()<entry[0]:
            diagnostic('youtube_resolve_cache_hit',video_id=video_id_for_log(url))
            return entry[1]
        if url in RESOLVE_INFLIGHT:
            event=RESOLVE_INFLIGHT[url]
            leader=False
        else:
            event=threading.Event()
            RESOLVE_INFLIGHT[url]=event
            leader=True
    if not leader:
        diagnostic('youtube_resolve_shared_request',video_id=video_id_for_log(url))
        if not event.wait(110):raise RuntimeError('YouTube shared resolver timed out')
        with RESOLVE_LOCK:
            entry=RESOLVE_CACHE.get(url)
            if entry and time.monotonic()<entry[0]:return entry[1]
        youtube_guard()
        raise RuntimeError('YouTube resolution unavailable; shared request failed')
    try:
        result=command_output([tool('yt-dlp'),'--no-playlist','--extractor-args',
            'youtube:player_client=web_embedded','-g','-f',
            '(best[protocol=m3u8_native]/best[protocol=m3u8]/best[ext=mp4]/best)',url],timeout=100).splitlines()[0]
        ttl=RESOLVE_TTL
        try:
            params=urllib.parse.parse_qs(urllib.parse.urlsplit(result).query)
            if 'expire' in params:
                ttl=min(ttl,max(0,int(params['expire'][0])-time.time()-90))
        except (ValueError,IndexError,TypeError):pass
        if ttl>15:
            with RESOLVE_LOCK:
                RESOLVE_CACHE[url]=(time.monotonic()+ttl,result)
                if len(RESOLVE_CACHE)>256:
                    for key,item in list(RESOLVE_CACHE.items()):
                        if time.monotonic()>=item[0]:RESOLVE_CACHE.pop(key,None)
                    while len(RESOLVE_CACHE)>256:RESOLVE_CACHE.pop(next(iter(RESOLVE_CACHE)))
            diagnostic('youtube_resolve_cached',video_id=video_id_for_log(url),ttl_seconds=int(ttl))
        return result
    finally:
        with RESOLVE_LOCK:
            RESOLVE_INFLIGHT.pop(url,None)
            event.set()

PLAYLIST_CACHE={}
PLAYLIST_LOCK=threading.RLock()
def playlist_entries(url):
    with PLAYLIST_LOCK:
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
    with PLAYLIST_LOCK:PLAYLIST_CACHE[url]=(time.monotonic(),entries)
    return entries

# Playlist rotations are independent of project.json and survive application restarts.
ROTATIONS_FILE=HERE/'playlist-progress.json'
ROTATION_LOCK=threading.RLock()
def rotation_load():
    try:
        obj=json.loads(ROTATIONS_FILE.read_text(encoding='utf-8'))
        return obj if isinstance(obj,dict) else {}
    except (OSError,ValueError):return {}
def rotation_save(all_states):
    fd,tmp=tempfile.mkstemp(prefix='.playlist-progress-',dir=HERE)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:json.dump(all_states,f,indent=2)
        os.replace(tmp,ROTATIONS_FILE)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
def rotation_get(source,entries):
    sid=str(source['id']); url=source['url']
    with ROTATION_LOCK:
        all_states=rotation_load(); state=all_states.get(sid)
        if not isinstance(state,dict) or state.get('url')!=url or not isinstance(state.get('order'),list):
            order=list(entries);random.shuffle(order)
            state={'url':url,'order':order,'position':0}
            all_states[sid]=state;rotation_save(all_states)
        else:
            # Incorporate newly added videos without replaying completed ones.
            available=set(entries);completed=[x for x in state['order'][:state.get('position',0)] if x in available]
            remaining=[x for x in state['order'][state.get('position',0):] if x in available]
            added=[x for x in entries if x not in set(completed+remaining)]
            random.shuffle(added)
            updated=completed+remaining+added
            if updated!=state['order'] or len(completed)!=state.get('position',0):
                state['order']=updated;state['position']=len(completed)
                all_states[sid]=state;rotation_save(all_states)
        if state['position']>=len(state['order']):
            state['order']=list(entries);random.shuffle(state['order']);state['position']=0
            all_states[sid]=state;rotation_save(all_states)
        return state['order'][state['position']]
def rotation_advance(source,entry,entries):
    with ROTATION_LOCK:
        all_states=rotation_load();state=all_states.get(str(source['id']))
        if not state or state.get('url')!=source['url']:return
        pos=state.get('position',0)
        if pos<len(state['order']) and state['order'][pos]==entry:
            state['position']=pos+1
            all_states[str(source['id'])]=state;rotation_save(all_states)
def rotation_reshuffle(source):
    with ROTATION_LOCK:
        all_states=rotation_load();all_states.pop(str(source['id']),None);rotation_save(all_states)

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
    guide_host=host or (lan_ip()+':'+str(PORT))
    lines=[f'#EXTM3U x-tvg-url="http://{guide_host}/epg.xml" url-tvg="http://{guide_host}/epg.xml"']
    for c,u in eligible():
        n=clean(c.get('number'));name=clean(c.get('name'))
        lines.append(f'#EXTINF:-1 tvg-id="lf.{n}" tvg-name="{name}" group-title="Link Forge",{name}')
        if source_type(u)=='direct':lines.append(u)
        elif source_type(u)=='youtube':
            lines.append(f'http://{guide_host}/subtitle-live/{urllib.parse.quote(str(c.get("id")),safe="")}/master.m3u8')
        else:lines.append(f'http://{guide_host}/hls/{urllib.parse.quote(str(c.get("id")),safe="")}/index.m3u8')
    return ('\n'.join(lines)+'\n').encode('utf-8')
def xmltv():
    root=Element('tv',{'generator-info-name':'Link Forge Lite'})
    from datetime import timedelta, timezone
    now=datetime.now(timezone.utc)
    start=(now-timedelta(hours=1)).replace(minute=0,second=0,microsecond=0)
    stop=start+timedelta(hours=48)
    stamp=lambda dt:dt.strftime('%Y%m%d%H%M%S +0000')
    for c,_ in eligible():
        number=str(c.get('number',''))
        name=str(c.get('name',''))
        node=SubElement(root,'channel',{'id':'lf.'+number})
        SubElement(node,'display-name').text=f'{number} {name}'
        programme=SubElement(root,'programme',{'channel':'lf.'+number,'start':stamp(start),'stop':stamp(stop)})
        SubElement(programme,'title',{'lang':'en'}).text=name
        SubElement(programme,'desc',{'lang':'en'}).text='Continuous programming. Individual titles are not scheduled.'
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
HLS_IDLE=240
HLS_SEGMENT_LIST=24  # 48 seconds of published segments, versus 24 seconds previously
HLS_DELETE_THRESHOLD=48  # retain older segments for slower clients

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

def hls_next_sequence(folder):
    """Continue MPEG-TS numbering even when FFmpeg starts a new clip."""
    manifest=folder/'index.m3u8'
    if not manifest.exists():return 0
    try:
        data=manifest.read_text(encoding='utf-8',errors='replace')
        match=re.search(r'^#EXT-X-MEDIA-SEQUENCE:(\d+)',data,re.M)
        base=int(match.group(1)) if match else 0
        count=len(re.findall(r'^#EXTINF:',data,re.M))
        return base+count
    except (OSError,ValueError):return 0

def continuous_playlist_worker(session, source_url, source):
    """Experimental: persistent HLS muxer with one normalized TS encoder per clip.

    This avoids restarting the HLS muxer and resetting its manifest for each clip.
    Clip encoding still uses a subprocess; true gapless playback is not guaranteed.
    """
    from concurrent.futures import ThreadPoolExecutor
    entries=playlist_entries(source_url)
    shuffled=source.get('playbackMode')=='shuffle_resume'
    preloader=ThreadPoolExecutor(max_workers=1,thread_name_prefix='forge-continuous-preload')
    mux=None
    failures=0;idx=0;future=None;next_url=None
    try:
        out=session['dir'];out.mkdir(parents=True,exist_ok=True)
        muxcmd=[tool('ffmpeg'),'-hide_banner','-loglevel','warning','-nostdin',
                '-fflags','+genpts+discardcorrupt','-f','mpegts','-i','pipe:0',
                '-map','0:v:0','-map','0:a:0?','-c','copy','-f','hls',
                '-hls_time','2','-hls_list_size','18','-hls_delete_threshold','18',
                '-hls_flags','delete_segments+omit_endlist+independent_segments',
                '-hls_segment_filename',str(out/'seg_%06d.ts'),str(out/'index.m3u8')]
        with open(out/'ffmpeg.log','ab') as log:
            log.write(b'\n=== continuous HLS muxer ===\n')
            mux=subprocess.Popen(muxcmd,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=log,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            session['proc']=mux
            diagnostic('continuous_mux_start',channel=session['cid'])
            while not session['stop'].is_set() and mux.poll() is None:
                current=rotation_get(source,entries) if shuffled else entries[idx]
                try:
                    resolved=future.result(timeout=100) if future and current==next_url else resolve(current)
                    diagnostic('continuous_clip_resolved',channel=session['cid'],video_id=video_id_for_log(current))
                    upcoming=entries[(idx+1)%len(entries)] if not shuffled else None
                    future=preloader.submit(resolve,upcoming) if upcoming else None
                    next_url=upcoming
                    # Normalize variable source frame rates, audio, and video sizes.
                    # Output is MPEG-TS into one long-running HLS muxer.
                    vf=['-vf','fps=30,scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p'] if source_quality(source)=='auto' else ['-vf','fps=30,scale=-2:trunc(min(ih\\,'+source_quality(source)+')/2)*2,format=yuv420p']
                    cmd=[tool('ffmpeg'),'-hide_banner','-loglevel','warning','-nostdin','-re','-i',resolved,
                         '-map','0:v:0','-map','0:a:0?',*vf,'-c:v','libx264','-preset','veryfast',
                         '-tune','zerolatency','-g','60','-keyint_min','60','-sc_threshold','0',
                         '-c:a','aac','-ar','44100','-ac','2','-b:a','128k',
                         '-mpegts_flags','+resend_headers','-f','mpegts','pipe:1']
                    diagnostic('continuous_clip_start',channel=session['cid'],video_id=video_id_for_log(current))
                    with open(out/'ffmpeg.log','ab') as cliplog:
                        clip=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=cliplog,
                            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                        session['clip_proc']=clip
                        while not session['stop'].is_set() and mux.poll() is None:
                            block=clip.stdout.read(188*64)
                            if not block:break
                            try:mux.stdin.write(block);mux.stdin.flush()
                            except (BrokenPipeError,OSError):break
                            if time.monotonic()-session['last']>HLS_IDLE:session['stop'].set()
                        if clip.poll() is None:
                            try:clip.terminate();clip.wait(timeout=3)
                            except Exception:clip.kill()
                        diagnostic('continuous_clip_exit',channel=session['cid'],returncode=clip.returncode)
                        success=clip.returncode==0
                    if shuffled:rotation_advance(source,current,entries)
                    idx=(idx+1)%len(entries)
                    failures=0 if success else failures+1
                    if failures>=min(8,len(entries)):break
                except Exception as exc:
                    if 'requests paused' in str(exc):
                        session['error']='YouTube verification required; cooldown active'
                        diagnostic('youtube_cooldown_stream_stopped',channel=session['cid'])
                        break
                    diagnostic('continuous_clip_skipped',channel=session['cid'],reason=type(exc).__name__)
                    if shuffled:rotation_advance(source,current,entries)
                    idx=(idx+1)%len(entries)
                    future=None;failures+=1
                    if failures>=min(8,len(entries)):break
    except Exception as exc:
        diagnostic('continuous_worker_error',channel=session['cid'],reason=type(exc).__name__)
        session['error']='Continuous muxer failed; see diagnostics'
    finally:
        session['stop'].set()
        if mux:
            try:
                if mux.stdin:mux.stdin.close()
                mux.terminate();mux.wait(timeout=4)
            except Exception:
                try:mux.kill()
                except Exception:pass
        preloader.shutdown(wait=False,cancel_futures=True)
        session['done']=True

def video_id_for_log(url):
    match=re.search(r'(?:v=|youtu\.be/)([\w-]{11})',url)
    return match.group(1) if match else 'unknown'

def hls_worker(session, source_url, source):
    try:
        entries=playlist_entries(source_url) if source_type(source_url)=='playlist' else [source_url]
        failures=0; idx=0
        from concurrent.futures import ThreadPoolExecutor
        preloader=ThreadPoolExecutor(max_workers=1,thread_name_prefix='forge-next')
        preloaded={}
        shuffled=source_type(source_url)=='playlist' and source.get('playbackMode')=='shuffle_resume'
        while not session['stop'].is_set():
            try:
                current=rotation_get(source,entries) if shuffled else entries[idx]
                diagnostic('source_resolve_start',channel=session['cid'],kind=source_type(current))
                future=preloaded.pop(current,None)
                resolved=future.result(timeout=100) if future else resolve(current)
                diagnostic('source_resolve_ok',channel=session['cid'],kind=source_type(current))
                # DXVA2 GPU scaling was validated on Intel UHD 630 with a fixed
                # QSV frame pool. Decoding remains software for source compatibility.
                # Fall back to the proven QSV encode and then x264 on startup failure.
                # Restore the original 1.1 Classic HLS muxing parameters.
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
                     quality_filter(source)+['-c:v','h264_qsv','-global_quality','23','-pix_fmt','nv12']),
                    ('software fallback (native resolution)', [],
                     quality_filter(source)+['-c:v','libx264','-preset','veryfast','-tune','zerolatency',
                      '-pix_fmt','yuv420p'])]
                preference=encoder_preference()
                if preference=='software':variants=variants[1:]
                elif preference=='qsv':variants=variants[:1]
                diagnostic('encoder_preference',channel=session['cid'],preference=preference)
                # Resolve the next sequential entry while FFmpeg handles this one.
                # Shuffled rotations are prefetched only after the current entry advances.
                if source_type(source_url)=='playlist' and not shuffled and len(entries)>1:
                    upcoming=entries[(idx+1)%len(entries)]
                    if upcoming not in preloaded:preloaded[upcoming]=preloader.submit(resolve,upcoming)
                completed=False
                sequence=hls_next_sequence(session['dir'])
                with CAPTION_TIMELINE_LOCK:
                    CAPTION_CLIPS.setdefault(session['cid'],[]).append((sequence,video_id_for_log(current)))
                diagnostic('caption_clip_boundary',channel=session['cid'],sequence=sequence,video_id=video_id_for_log(current))
                diagnostic('clip_start',channel=session['cid'],video_id=re.search(r'(?:v=|youtu\.be/)([\w-]{11})',current).group(1) if re.search(r'(?:v=|youtu\.be/)([\w-]{11})',current) else 'unknown',sequence=sequence,shuffle=shuffled)
                for mode,input_opts,video_opts in variants:
                    if session['stop'].is_set():break
                    # Match 1.1 fallback: discard partial segments before software retry.
                    if mode!=variants[0][0]:
                        for old in session['dir'].glob('seg_*.ts'):
                            try:old.unlink()
                            except OSError:pass
                        try:(session['dir']/'index.m3u8').unlink()
                        except OSError:pass
                    progress_file=session['dir']/'encoder-progress.txt'
                    try:progress_file.unlink(missing_ok=True)
                    except OSError:pass
                    cmd=[tool('ffmpeg'),'-hide_banner','-loglevel','warning','-nostdin','-stats_period','2','-progress',str(progress_file),'-re']+input_opts+['-i',resolved]+video_opts+output
                    session['mode']=mode
                    diagnostic('encoder_start',channel=session['cid'],mode=mode)
                    print('HLS encoding:',mode,'channel:',session.get('cid','unknown'),flush=True)
                    with open(session['dir']/'ffmpeg.log','ab') as log:
                        log.write(('\n=== '+mode+' ===\n').encode());log.flush()
                        proc=subprocess.Popen(cmd,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=log,
                            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                        session['proc']=proc
                        # Watch startup: if QSV fails to initialize, automatically retry x264.
                        startup=time.monotonic()
                        last_sample=0
                        while proc.poll() is None and not session['stop'].wait(1):
                            if time.monotonic()-last_sample>=10:
                                stats=read_progress(progress_file)
                                if stats:diagnostic('encoder_performance',channel=session['cid'],mode=mode,**stats)
                                last_sample=time.monotonic()
                            if time.monotonic()-session['last']>HLS_IDLE:session['stop'].set()
                        if proc.poll() is None:stop_session(session)
                        next_sequence=hls_next_sequence(session['dir'])
                        produced=next_sequence>sequence
                        diagnostic('encoder_exit',channel=session['cid'],mode=mode,returncode=proc.returncode,sequence_start=sequence,sequence_end=next_sequence,produced=produced)
                        completed=(proc.returncode==0 and produced and not session['stop'].is_set())
                        if proc.returncode not in (None,0) or not produced:
                            try:
                                tail=(session['dir']/'ffmpeg.log').read_text(encoding='utf-8',errors='replace')[-1200:]
                            except OSError:tail='unavailable'
                            # Signed URLs can appear in ffmpeg errors: redact query strings.
                            tail=re.sub(r'https?://[^\s\]\["\']+', '[REDACTED_URL]', tail)
                            diagnostic('encoder_failed',channel=session['cid'],mode=mode,returncode=proc.returncode,produced=produced,stderr=tail)
                        if mode!=variants[-1][0] and proc.returncode not in (None,0) and not produced and not session['stop'].is_set():
                            print(mode,'failed before HLS output; trying next encoding mode.',flush=True)
                            continue
                        break
                if session['stop'].is_set():break
                if shuffled and completed:rotation_advance(source,current,entries)
                failures=0 if completed else failures+1
                if shuffled and not completed:
                    diagnostic('playlist_item_skipped',channel=session['cid'],reason='encoder_failed')
                    rotation_advance(source,current,entries)
                    if failures>=min(8,len(entries)):break
                    continue
                if source_type(source_url)=='twitch' or failures>=min(5,len(entries)):break
                idx=(idx+1)%len(entries)
                # The next clip continues the segment numbering and marks a discontinuity.
            except Exception as exc:
                if source_type(source_url) in ('playlist','youtube') and 'requests paused' in str(exc):
                    session['error']='YouTube verification required; cooldown active'
                    diagnostic('youtube_cooldown_stream_stopped',channel=session['cid'])
                    break
                if source_type(source_url)=='playlist':
                    diagnostic('playlist_item_skipped',channel=session['cid'],reason=type(exc).__name__)
                    failures+=1
                    if shuffled:rotation_advance(source,current,entries)
                    else:idx=(idx+1)%len(entries)
                    if failures<min(8,len(entries)):continue
                session['error']=str(exc)[-300:]
                diagnostic('worker_exception',channel=session['cid'],error=re.sub(r'https?://[^\s]+','[REDACTED_URL]',str(exc)))
                print('HLS worker error:',session['error'])
                break
    finally:
        if 'preloader' in locals():preloader.shutdown(wait=False,cancel_futures=True)
        session['done']=True

def caption_clock_sampler(session):
    """Capture transport timestamps even while captions are switched OFF.

    HLS deletes old segments, so a late subtitle request cannot probe the
    beginning of a clip unless we cache its initial transport clock early.
    """
    cid=session['cid']
    while not session['stop'].is_set() and not session.get('done'):
        try:
            manifest=session['dir']/'index.m3u8'
            if manifest.is_file():
                data=manifest.read_text(encoding='utf-8',errors='replace')
                numbers=[int(n) for n in re.findall(r'(?m)^seg_(\d{6})\.ts\s*$',data)]
                with CAPTION_TIMELINE_LOCK:
                    boundaries=list(CAPTION_CLIPS.get(cid,[]))
                # Probe only the first segment of each clip, plus the oldest
                # currently published segment for fallback timing diagnostics.
                for base,_ in boundaries:
                    if base in numbers:segment_pts(cid,base)
                if numbers:segment_pts(cid,numbers[0])
        except (OSError,ValueError) as exc:
            diagnostic('caption_clock_sampler_error',channel=cid,reason=type(exc).__name__)
        session['stop'].wait(0.5)

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
        with CAPTION_TIMELINE_LOCK:
            CAPTION_TIMELINES.pop(cid,None)
            CAPTION_PTS.pop(cid,None)
            CAPTION_CLIPS.pop(cid,None)
        session={'cid':cid,'dir':folder,'last':time.monotonic(),'stop':threading.Event(),
                 'done':False,'error':None,'proc':None}
        HLS_SESSIONS[cid]=session
        threading.Thread(target=caption_clock_sampler,args=(session,),daemon=True).start()
        worker=hls_worker  # 1.2.1g comparison: always use restored Classic engine
        threading.Thread(target=worker,args=(session,source['url'],source),daemon=True).start()
        return session

@atexit.register
def shutdown_hls():
    for session in list(HLS_SESSIONS.values()):
        session['stop'].set()
        stop_session(session)
    shutil.rmtree(HLS_ROOT,ignore_errors=True)

class ForgeHTTPServer(ThreadingHTTPServer):
    # The stdlib default listen backlog is small; allow concurrent HLS viewers
    # to connect without waiting behind queued segment/manifest requests.
    request_queue_size=128
    daemon_threads=True
    allow_reuse_address=True


# Opt-in WebVTT/HLS subtitle compatibility probe. This is deliberately separate
# from production HLS streams: it displays test text, not real captions.
def subtitle_probe_master(cid, host):
    base=f'http://{host}/subtitle-test/{urllib.parse.quote(cid,safe="")}'
    return ('#EXTM3U\n#EXT-X-VERSION:3\n'
            f'#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="forge-test",NAME="Forge Subtitle Test",LANGUAGE="en",DEFAULT=NO,AUTOSELECT=YES,FORCED=NO,URI="{base}/subtitles.m3u8"\n'
            f'#EXT-X-STREAM-INF:BANDWIDTH=4000000,CODECS="avc1.42e01e,mp4a.40.2",SUBTITLES="forge-test"\n'
            f'http://{host}/hls/{urllib.parse.quote(cid,safe="")}/index.m3u8\n').encode()

def subtitle_probe_playlist(cid):
    session=ensure_hls(cid)
    if not session:return None
    index=session['dir']/'index.m3u8'
    if not index.is_file():return None
    raw=index.read_text(encoding='utf-8',errors='replace')
    lines=[]
    for line in raw.splitlines():
        if line.startswith('#EXT-X-KEY:') or line.startswith('#EXT-X-MAP:'):
            continue
        if re.fullmatch(r'seg_[0-9]{6}\.ts',line.strip()):
            lines.append('sub_'+line[4:-3]+'.vtt')
        else:lines.append(line)
    return ('\n'.join(lines)+'\n').encode()

def subtitle_probe_vtt(number):
    # Each segment uses absolute media time, matching the 4-second test HLS
    # segment target. This is a compatibility experiment, not caption sync.
    start=number*4;end=start+3
    def stamp(seconds):return f'{seconds//3600:02}:{(seconds//60)%60:02}:{seconds%60:02}.000'
    return f'WEBVTT\n\n{stamp(start)} --> {stamp(end)}\nNTV FORGE SUBTITLE TEST • segment {number}\n'.encode('utf-8')

# Real captions beta: only individual YouTube videos. Playlist clips require
# per-clip timestamp remapping and are intentionally not enabled yet.
CAPTION_CACHE={}
CAPTION_LOCK=threading.RLock()
CAPTION_TIMELINES={}  # observed segment durations, indexed by channel
CAPTION_PTS={}  # actual MPEG-TS segment start times, cached by channel and sequence
CAPTION_CLIPS={}  # (first HLS sequence, video ID) for each FFmpeg clip
CAPTION_TIMELINE_LOCK=threading.RLock()
def caption_timestamp(value):
    m=re.match(r'(?:(\d+):)?(\d{2}):(\d{2})[.,](\d{3})',value.strip())
    if not m:return None
    return (int(m.group(1) or 0)*3600+int(m.group(2))*60+int(m.group(3)))+int(m.group(4))/1000

def caption_cues(vtt):
    cues=[]
    for block in re.split(r'\r?\n\s*\r?\n',vtt.replace('\ufeff','')):
        lines=block.splitlines()
        for i,line in enumerate(lines):
            if '-->' not in line:continue
            a,b=line.split('-->',1)
            start=caption_timestamp(a);end=caption_timestamp(b.split()[0])
            if start is not None and end is not None and end>start:
                body='\n'.join(lines[i+1:]).strip()
                if body:cues.append((start,end,body))
            break
    return cues

def load_youtube_captions(cid):
    src=channel_source(cid)
    if not src or source_type(src.get('url',''))!='youtube':return None
    url=src['url']
    with CAPTION_LOCK:
        cached=CAPTION_CACHE.get(url)
        if cached and time.monotonic()-cached[0]<21600:return cached[1]
    import tempfile
    with tempfile.TemporaryDirectory(prefix='forge-captions-') as tmp:
        template=str(Path(tmp)/'captions.%(ext)s')
        cmd=[tool('yt-dlp'),'--no-playlist','--skip-download','--write-subs',
             '--write-auto-subs','--sub-langs','en.*,en','--sub-format','vtt',
             '-o',template,url]
        try:
            command_output(cmd,timeout=100)
            files=list(Path(tmp).glob('*.vtt'))
            if not files:raise RuntimeError('No English WebVTT captions available')
            # Prefer explicitly named English subtitles when multiple exist.
            files.sort(key=lambda f:(0 if '.en.' in f.name else 1,len(f.name)))
            cues=caption_cues(files[0].read_text(encoding='utf-8-sig',errors='replace'))
            if not cues:raise RuntimeError('Caption file contains no readable cues')
            with CAPTION_LOCK:CAPTION_CACHE[url]=(time.monotonic(),cues)
            diagnostic('real_captions_ready',channel=cid,cues=len(cues))
            return cues
        except Exception as exc:
            diagnostic('real_captions_unavailable',channel=cid,reason=type(exc).__name__)
            return None

def caption_manifest_segments(cid):
    session=ensure_hls(cid)
    if not session:return None
    index=session['dir']/'index.m3u8'
    if not index.is_file():return None
    lines=index.read_text(encoding='utf-8',errors='replace').splitlines()
    seq=0
    for line in lines:
        if line.startswith('#EXT-X-MEDIA-SEQUENCE:'):
            try:seq=int(line.split(':',1)[1])
            except ValueError:pass
    # HLS segment numbers track video timeline for a single video. Use the
    # advertised durations for cues within the current window.
    segments=[];duration=None
    for line in lines:
        if line.startswith('#EXTINF:'):
            try:duration=float(line.split(':',1)[1].split(',')[0])
            except ValueError:duration=4.0
        elif re.fullmatch(r'seg_[0-9]{6}\.ts',line.strip()):
            number=int(line[4:10]);segments.append((number,duration or 4.0));duration=None
    # Remember observed EXTINF durations across the sliding live playlist.
    # Do not assume each segment is exactly four seconds: keyframe boundaries vary.
    with CAPTION_TIMELINE_LOCK:
        known=CAPTION_TIMELINES.setdefault(cid,{})
        for number,duration in segments:
            known[number]=duration
        # Keep enough history for long-form video, bounded to 20k segments.
        if len(known)>20000:
            for old in sorted(known)[:len(known)-20000]:known.pop(old,None)
    return lines,segments,seq

def segment_pts(cid, number):
    """Read the actual MPEG-TS presentation clock, never a wall-clock guess.

    ffprobe is run only once per segment. The probe runs against a local TS
    file, not a remote URL, so no signed source information is exposed.
    """
    with CAPTION_TIMELINE_LOCK:
        cache=CAPTION_PTS.setdefault(cid,{})
        if number in cache:return cache[number]
    session=HLS_SESSIONS.get(cid)
    if not session:return None
    segment=session['dir']/f'seg_{number:06d}.ts'
    if not segment.is_file():return None
    try:
        cmd=[tool('ffprobe'),'-v','error','-show_entries','format=start_time',
             '-of','default=noprint_wrappers=1:nokey=1',str(segment)]
        result=subprocess.run(cmd,capture_output=True,text=True,timeout=5,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        pts=float(result.stdout.strip()) if result.returncode==0 else None
        if pts is not None and not (0<=pts<1e9):pts=None
    except (OSError,ValueError,subprocess.TimeoutExpired):pts=None
    if pts is not None:
        with CAPTION_TIMELINE_LOCK:
            CAPTION_PTS.setdefault(cid,{})[number]=pts
    return pts

def caption_window(cid,number):
    with CAPTION_TIMELINE_LOCK:
        known=CAPTION_TIMELINES.get(cid,{}).copy()
        boundaries=list(CAPTION_CLIPS.get(cid,[]))
    base=max((seq for seq,_ in boundaries if seq<=number),default=0)
    missing=sum(1 for n in range(base,number) if n not in known)
    estimated=sum(known.get(n,4.0) for n in range(base,number))
    duration=known.get(number,4.0)
    # Prefer timestamps in the video transport stream, not a reconstructed
    # timeline that can lose the first segments of a sliding live playlist.
    pts=segment_pts(cid,number)
    base_pts=segment_pts(cid,base)
    if pts is not None and base_pts is not None and pts>=base_pts:
        start=pts-base_pts
        timing='mpegts'
    else:
        start=estimated
        timing='duration_fallback'
    return start,duration,missing,pts,timing

def hls_with_clip_discontinuities(cid, raw):
    """Publish clip boundary discontinuities on both video and WebVTT renditions.

    FFmpeg restarts its MPEG transport timestamp clock for each loop. Clients
    need EXT-X-DISCONTINUITY even when segment numbering remains continuous.
    DISCONTINUITY-SEQUENCE accounts for clip boundaries outside the sliding window.
    """
    lines=raw.splitlines()
    with CAPTION_TIMELINE_LOCK:
        boundaries=sorted({seq for seq,_ in CAPTION_CLIPS.get(cid,[])})
    if len(boundaries)<2:return raw
    first=next((int(m.group(1)) for line in lines if (m:=re.fullmatch(r'seg_(\d{6})\.ts',line.strip()))),None)
    if first is None:return raw
    prior=sum(1 for seq in boundaries[1:] if seq<first)
    boundaries_here=set(seq for seq in boundaries[1:] if seq>=first)
    output=[];pending=False;added=0
    for line in lines:
        if line.startswith('#EXT-X-DISCONTINUITY-SEQUENCE:'):
            continue
        if line.startswith('#EXTINF:'):
            pending=True
        m=re.fullmatch(r'seg_(\d{6})\.ts',line.strip())
        if m:
            seq=int(m.group(1))
            if seq in boundaries_here:
                # Insert immediately before EXTINF, not between duration and URI.
                pos=len(output)-1 if output and output[-1].startswith('#EXTINF:') else len(output)
                if pos and output[pos-1]=='#EXT-X-DISCONTINUITY':
                    pass
                else:
                    output.insert(pos,'#EXT-X-DISCONTINUITY');added+=1
            pending=False
        output.append(line)
    if prior or added:
        pos=next((i+1 for i,line in enumerate(output) if line.startswith('#EXT-X-MEDIA-SEQUENCE:')),1)
        output.insert(pos,f'#EXT-X-DISCONTINUITY-SEQUENCE:{prior}')
    return '\n'.join(output)+'\n'

def real_caption_playlist(cid):
    result=caption_manifest_segments(cid)
    if not result:return None
    lines,segments,_=result
    lines=hls_with_clip_discontinuities(cid,'\n'.join(lines)+'\n').splitlines()
    out=[]
    for line in lines:
        if re.fullmatch(r'seg_[0-9]{6}\.ts',line.strip()):out.append('caption_'+line[4:-3]+'.vtt')
        elif not line.startswith(('#EXT-X-KEY:','#EXT-X-MAP:')):out.append(line)
    return ('\n'.join(out)+'\n').encode()

def real_caption_vtt(cid,number,cues):
    # FFmpeg's 4-second HLS target can vary at keyframe boundaries. Sum actual
    # segment durations when available; otherwise approximate by target time.
    result=caption_manifest_segments(cid)
    start,duration,missing,pts,timing=caption_window(cid,number)
    end=start+duration
    def stamp(t):
        ms=round(t*1000)
        return f'{ms//3600000:02}:{(ms//60000)%60:02}:{(ms//1000)%60:02}.{ms%1000:03}'
    # MPEGTS maps the WebVTT cue clock to the video's actual transport
    # timestamps. This is essential when a player enables subtitles late.
    # LOCAL is the same clip-relative time used by the cue timestamps.
    mapping=(f'X-TIMESTAMP-MAP=LOCAL={stamp(start)},MPEGTS={round(pts*90000)}'
             if pts is not None else None)
    result=['WEBVTT']
    if mapping:result.append(mapping)
    result.append('')
    matches=0
    for a,b,body in cues:
        if a<end and b>start:
            result.extend([f'{stamp(max(a,start))} --> {stamp(min(b,end))}',body,''])
            matches+=1
    with CAPTION_TIMELINE_LOCK:
        boundaries=list(CAPTION_CLIPS.get(cid,[]))
    base=max((seq for seq,_ in boundaries if seq<=number),default=0)
    diagnostic('caption_segment',channel=cid,segment=number,clip_sequence=base,start=round(start,3),end=round(end,3),cues=matches,missing_history=missing,timing=timing,pts=round(pts,3) if pts is not None else "unavailable",timestamp_map=bool(mapping))
    return ('\n'.join(result)+'\n').encode('utf-8')

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
        if p in ('/forge-logo.png','/forge-logo-small.png'):
            return self.reply(200,(ASSETS/p.lstrip('/')).read_bytes(),'image/png')
        if p=='/api/files':
            if self.client_address[0] not in ('127.0.0.1','::1'):
                return self.reply(403,b'{"error":"Local host only"}','application/json')
            try:
                q=urllib.parse.parse_qs(urlsplit(self.path).query)
                requested=q.get('path',[''])[0].strip()
                if len(requested)>2048 or '\x00' in requested:raise ValueError('Invalid path')
                target=Path(requested).expanduser() if requested else Path.home()
                if not target.is_dir():raise ValueError('Directory unavailable')
                files=[]
                for item in target.iterdir():
                    try:
                        if item.is_dir():files.append({'name':item.name,'path':str(item),'directory':True})
                        elif item.is_file() and item.suffix.lower() in ('.csv','.json'):
                            files.append({'name':item.name,'path':str(item),'directory':False})
                    except (PermissionError,OSError):continue
                    if len(files)>=600:break
                files.sort(key=lambda f:(not f['directory'],f['name'].casefold()))
                return self.reply(200,json.dumps({'path':str(target),'parent':str(target.parent),'items':files}).encode(),'application/json')
            except (ValueError,OSError) as e:return self.reply(400,json.dumps({'error':str(e)}).encode(),'application/json')
        if p=='/api/file-read':
            if self.client_address[0] not in ('127.0.0.1','::1'):
                return self.reply(403,b'Local host only','text/plain')
            try:
                q=urllib.parse.parse_qs(urlsplit(self.path).query)
                requested=q.get('path',[''])[0]
                if len(requested)>2048 or '\x00' in requested:raise ValueError('Invalid path')
                target=Path(requested).expanduser()
                if target.suffix.lower() not in ('.csv','.json') or not target.is_file() or target.stat().st_size>2_000_000:
                    raise ValueError('Only CSV/JSON files up to 2 MB are supported')
                return self.reply(200,target.read_bytes(),'text/plain; charset=utf-8')
            except (ValueError,OSError) as e:return self.reply(400,str(e).encode(),'text/plain')
        if p=='/api/achievement':
            obj=get_project();return self.reply(200,json.dumps({'preservationist':obj.get('achievements',{}).get('preservationist',{})}).encode(),'application/json')
        if p=='/forge-picker.js':return self.reply(200,(ASSETS/'forge_picker.js').read_bytes(),'application/javascript; charset=utf-8')
        if p=='/archive':
            if not get_project().get('achievements',{}).get('preservationist',{}).get('unlocked'):
                return self.reply(404,b'Workspace unavailable','text/plain')
            return self.reply(200,(ASSETS/'archive.html').read_bytes(),'text/html; charset=utf-8')
        if p=='/api/folders':
            if self.client_address[0] not in ('127.0.0.1','::1'):
                return self.reply(403,b'{"error":"Host-only folder browser"}','application/json')
            try:
                query=urllib.parse.parse_qs(urlsplit(self.path).query)
                requested=query.get('path',[''])[0].strip()
                if len(requested)>2048 or '\x00' in requested:raise ValueError('Invalid folder path')
                if not requested:
                    if os.name=='nt':
                        import ctypes
                        mask=ctypes.windll.kernel32.GetLogicalDrives()
                        roots=[str(chr(65+i))+':\\' for i in range(26) if mask&(1<<i)]
                    else:roots=[str(Path.home()),'/mnt','/media']
                    result={'path':'','parent':'','folders':[{'name':r,'path':r} for r in roots if Path(r).is_dir()]}
                else:
                    target=Path(requested).expanduser()
                    if not target.is_dir():raise ValueError('Folder does not exist or is inaccessible')
                    folders=[]
                    for item in target.iterdir():
                        try:
                            if item.is_dir():folders.append({'name':item.name,'path':str(item)})
                        except (PermissionError,OSError):continue
                        if len(folders)>=500:break
                    folders.sort(key=lambda x:x['name'].casefold())
                    parent=target.parent
                    result={'path':str(target),'parent':str(parent) if parent!=target else '', 'folders':folders}
                return self.reply(200,json.dumps(result).encode(),'application/json')
            except (OSError,ValueError) as exc:
                return self.reply(400,json.dumps({'error':str(exc)}).encode(),'application/json')
        if p=='/api/archive/status':
            if not get_project().get('achievements',{}).get('preservationist',{}).get('unlocked'):
                return self.reply(404,b'Workspace unavailable','text/plain')
            if self.client_address[0] not in ('127.0.0.1','::1'):return self.reply(403,b'Archive controls are local-only','text/plain')
            job=archive_forge.status(urllib.parse.parse_qs(urlsplit(self.path).query).get('id',[''])[0])
            return self.reply(200,json.dumps(job or {'state':'not_found'}).encode(),'application/json')
        if p=='/link':return self.reply(200,(ASSETS/'index.html').read_bytes(),'text/html; charset=utf-8')
        if p=='/forge':return self.reply(200,(ASSETS/'channel_forge'/'index.html').read_bytes(),'text/html; charset=utf-8')
        if p=='/forge/logo.svg':return self.reply(200,(ASSETS/'channel_forge'/'forge-logo.svg').read_bytes(),'image/svg+xml')
        if p=='/forge/api/sample':return self.reply(200,(ASSETS/'channel_forge'/'channels.csv').read_bytes(),'text/csv; charset=utf-8')
        if p=='/channels.m3u':return self.reply(200,m3u(self.headers.get('Host') if self.headers.get('Host','').split(':')[0] in ['localhost','127.0.0.1',*lan_ips()] else None),'audio/x-mpegurl; charset=utf-8')
        if p.startswith('/subtitle-test/'):return self.subtitle_test(p)
        if p.startswith('/subtitle-live/'):return self.subtitle_live(p)
        if p.startswith('/subtitle-preview/'):
            cid=urllib.parse.unquote(p[len('/subtitle-preview/'):].strip('/'))
            src=channel_source(cid)
            if not src or source_type(src.get('url',''))!='youtube':return self.reply(404,b'Not a single YouTube video channel','text/plain')
            cues=load_youtube_captions(cid)
            if not cues:return self.reply(404,b'Captions unavailable','text/plain')
            sample=[{'start':round(a,3),'end':round(b,3),'text':body} for a,b,body in cues[:25]]
            manifest=caption_manifest_segments(cid)
            segment_info=[]
            if manifest:
                for number,duration in manifest[1]:
                    start,actual,missing,pts,timing=caption_window(cid,number)
                    segment_info.append({'segment':number,'start':round(start,3),'duration':round(actual,3),'missing_history':missing,'matching_cues':sum(a<start+actual and b>start for a,b,_ in cues)})
            return self.reply(200,json.dumps({'channel':cid,'total_cues':len(cues),'first_cue':round(cues[0][0],3),'last_cue':round(cues[-1][1],3),'sample_cues':sample,'segments':segment_info},indent=2).encode(),'application/json; charset=utf-8')
        if p.startswith('/hls/'):return self.hls(p)
        if p.startswith('/play/'):return self.play(urllib.parse.unquote(p[len('/play/'):]))
        if p=='/api/encoder':return self.reply(200,json.dumps({'mode':encoder_preference(),'restart_stream_required':True}).encode(),'application/json')
        if p=='/api/diagnostics':return self.diagnostics()
        if p=='/api/events':return self.events()
        if p=='/api/youtube-guard':
            with YOUTUBE_GUARD_LOCK:remaining=max(0,int(YOUTUBE_BLOCKED_UNTIL-time.monotonic()))
            return self.reply(200,json.dumps({'blocked':bool(remaining),'remaining_seconds':remaining}).encode(),'application/json')
        if p=='/api/tools':return self.reply(200,json.dumps({k:bool(bundled_tool(k) or shutil.which(k)) for k in ('yt-dlp','streamlink','ffmpeg')}).encode(),'application/json')
        if p in ('/guide.xml','/epg.xml','/xmltv.xml'):return self.reply(200,xmltv(),'application/xml; charset=utf-8')
        if p=='/favicon.ico':return self.reply(204,b'','image/x-icon')
        if p=='/api/status':return self.source_status(urllib.parse.parse_qs(urlsplit(self.path).query).get('id',[''])[0])
        if p=='/api/rotation':return self.rotation_status(urllib.parse.parse_qs(urlsplit(self.path).query).get('id',[''])[0])
        if p=='/api/project':return self.reply(200,json.dumps(get_project()).encode(),'application/json')
        if p=='/api/network':return self.reply(200,json.dumps({'ip':lan_ip(),'ips':lan_ips()}).encode(),'application/json')
        self.reply(404,b'Not found','text/plain')
    def events(self):
        events=[]
        try:
            with DIAG_LOG.open('rb') as f:
                f.seek(0,2);size=f.tell();f.seek(max(0,size-100000));raw=f.read().decode('utf-8','replace')
            for line in raw.splitlines()[-100:]:
                try:
                    obj=json.loads(line)
                    if isinstance(obj,dict):
                        events.append({'time':str(obj.get('time',''))[-8:], 'event':str(obj.get('event',''))[:60], 'channel':str(obj.get('channel',''))[:50], 'mode':str(obj.get('mode',''))[:70]})
                except ValueError:pass
        except OSError:pass
        return self.reply(200,json.dumps({'version':VERSION,'events':events[-25:]}).encode(),'application/json')

    def diagnostics(self):
        import io
        output=io.BytesIO()
        with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr('version.txt', VERSION+'\\n')
            z.writestr('tools.txt','\\n'.join(k+': '+('found' if bundled_tool(k) or shutil.which(k) else 'missing') for k in ('ffmpeg','streamlink','yt-dlp')))
            if DIAG_LOG.exists():
                z.writestr('forge-diagnostics.log',DIAG_LOG.read_bytes()[-300000:])
            old_log=HERE/'ntv-forge.log'
            if old_log.exists():z.writestr('ntv-forge.log',old_log.read_bytes()[-300000:])
            z.writestr('README.txt','Diagnostic report. Review before sharing; source URLs and credentials are intentionally excluded.\\n')
        data=output.getvalue()
        self.send_response(200)
        self.send_header('Content-Type','application/zip')
        self.send_header('Content-Disposition','attachment; filename="NTV-Forge-Diagnostics.zip"')
        self.send_header('Content-Length',str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def rotation_status(self,sid):
        source=next((x for x in get_project()['sources'] if isinstance(x,dict) and x.get('id')==sid),None)
        if not source:return self.reply(404,b'Source not found','text/plain')
        with ROTATION_LOCK:state=rotation_load().get(sid,{})
        return self.reply(200,json.dumps({'position':state.get('position',0),'total':len(state.get('order',[]))}).encode(),'application/json')
    def source_status(self, sid):
        project=get_project()
        source=next((x for x in project['sources'] if isinstance(x,dict) and x.get('id')==sid),None)
        if not source:return self.reply(404,b'Source not found','text/plain')
        url=source.get('url','')
        kind=source_type(url)
        result={'state':'unknown','message':'Could not confirm availability.'}
        if kind in ('youtube','playlist'):
            try:youtube_guard()
            except RuntimeError as exc:
                return self.reply(200,json.dumps({'state':'blocked','message':str(exc)}).encode(),'application/json')
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
            if 'requests paused' in str(exc):
                return self.reply(200,json.dumps({'state':'blocked','message':str(exc)}).encode(),'application/json')
            result={'state':'unknown','message':'Could not verify source right now. It may be offline, restricted, or temporarily unavailable.'}
        return self.reply(200,json.dumps(result).encode(),'application/json')

    def subtitle_live(self,path):
        parts=path.split('/')
        if len(parts)!=4:return self.reply(404,b'Not found','text/plain')
        cid=urllib.parse.unquote(parts[2]);name=parts[3]
        src=channel_source(cid)
        if not src or source_type(src.get('url',''))!='youtube':
            return self.reply(400,b'Real captions beta supports single YouTube videos only','text/plain')
        if name=='master.m3u8':
            # Captions are optional: their absence must never block video playback.
            try:
                cues=load_youtube_captions(cid)
            except Exception as exc:
                diagnostic('caption_optional_error',channel=cid,error=str(exc)[:250])
                cues=[]
            host=self.headers.get('Host','')
            if not re.fullmatch(r'[A-Za-z0-9.\-\[\]:]+',host):host=lan_ip()+':'+str(PORT)
            base=f'http://{host}/subtitle-live/{urllib.parse.quote(cid,safe="")}'
            caption_line=(f'#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="forge-captions",NAME="English (Forge)",LANGUAGE="en",DEFAULT=NO,AUTOSELECT=YES,FORCED=NO,URI="{base}/subtitles.m3u8"\n' if cues else '')
            subtitle_attr=',SUBTITLES="forge-captions"' if cues else ''
            manifest=('#EXTM3U\n#EXT-X-VERSION:3\n'
                      +caption_line
                      +f'#EXT-X-STREAM-INF:BANDWIDTH=4000000,CODECS="avc1.42e01e,mp4a.40.2"{subtitle_attr}\n'
                      +f'http://{host}/hls/{urllib.parse.quote(cid,safe="")}/index.m3u8\n').encode()
            diagnostic('automatic_caption_master',channel=cid,available=bool(cues))
            return self.reply(200,manifest,'application/vnd.apple.mpegurl')
        if name=='subtitles.m3u8':
            if not load_youtube_captions(cid):return self.reply(404,b'Captions unavailable','text/plain')
            data=real_caption_playlist(cid)
            if not data:return self.reply(503,b'HLS not ready','text/plain')
            diagnostic('caption_playlist',channel=cid,bytes=len(data))
            return self.reply(200,data,'application/vnd.apple.mpegurl')
        m=re.fullmatch(r'caption_([0-9]{6})\.vtt',name)
        if m:
            cues=load_youtube_captions(cid)
            if not cues:return self.reply(404,b'Captions unavailable','text/plain')
            return self.reply(200,real_caption_vtt(cid,int(m.group(1)),cues),'text/vtt; charset=utf-8')
        return self.reply(404,b'Not found','text/plain')

    def subtitle_test(self,path):
        parts=path.split('/')
        if len(parts)!=4:return self.reply(404,b'Not found','text/plain')
        cid=urllib.parse.unquote(parts[2]);name=parts[3]
        src=channel_source(cid)
        if not src or source_type(src.get('url','')) not in ('youtube','twitch','playlist'):
            return self.reply(404,b'Channel not found','text/plain')
        if name=='master.m3u8':
            host=self.headers.get('Host','')
            if not re.fullmatch(r'[A-Za-z0-9.\-\[\]:]+',host):host=lan_ip()+':'+str(PORT)
            diagnostic('subtitle_probe_master',channel=cid)
            return self.reply(200,subtitle_probe_master(cid,host),'application/vnd.apple.mpegurl')
        if name=='subtitles.m3u8':
            data=subtitle_probe_playlist(cid)
            if data is None:return self.reply(503,b'Video HLS not ready; start the normal stream first','text/plain')
            diagnostic('subtitle_probe_playlist',channel=cid)
            return self.reply(200,data,'application/vnd.apple.mpegurl')
        m=re.fullmatch(r'sub_([0-9]{6})\.vtt',name)
        if m:
            return self.reply(200,subtitle_probe_vtt(int(m.group(1))),'text/vtt; charset=utf-8')
        return self.reply(404,b'Not found','text/plain')

    def hls(self,path):
        request_started=time.monotonic()
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
            elapsed_ms=round((time.monotonic()-request_started)*1000)
            diagnostic('hls_missing',channel=cid,file=filename,client=self.client_address[0],wait_ms=elapsed_ms,session_done=session['done'])
            msg=('HLS not ready: '+(session.get('error') or 'try again shortly')).encode()
            return self.reply(503 if not session['done'] else 502,msg,'text/plain')
        # Segment files are immutable once published; read once and share bytes across clients.
        try:
            body=file.read_bytes()
            if filename=='index.m3u8':
                body=hls_with_clip_discontinuities(cid,body.decode('utf-8',errors='replace')).encode('utf-8')
        except OSError:
            diagnostic('hls_read_failed',channel=cid,file=filename,client=self.client_address[0])
            return self.reply(503,b'Segment temporarily unavailable','text/plain')
        elapsed_ms=round((time.monotonic()-request_started)*1000)
        # Sample manifest requests; record all slow requests and segment errors.
        if elapsed_ms>=150 or (filename=='index.m3u8' and time.monotonic()-session.get('last_manifest_sample',0)>15):
            if filename=='index.m3u8':session['last_manifest_sample']=time.monotonic()
            try:mtime_age_ms=round((time.time()-file.stat().st_mtime)*1000)
            except OSError:mtime_age_ms=-1
            diagnostic('hls_delivery',channel=cid,file=filename,client=self.client_address[0],duration_ms=elapsed_ms,bytes=len(body),file_age_ms=mtime_age_ms,encoder=session.get('mode','unknown'))
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
        if path=='/api/achievement':
            if self.client_address[0] not in ('127.0.0.1','::1'):
                return self.reply(403,b'{"error":"Local host only"}','application/json')
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<1024:raise ValueError('Invalid request')
                obj=json.loads(self.rfile.read(size))
                if obj.get('action') not in ('unlock','reset'):raise ValueError('Invalid action')
                if obj['action']=='unlock' and obj.get('code','').upper()!='↑↑↓↓←→←→BA':
                    raise ValueError('Invalid sequence')
                from datetime import datetime,timezone
                with LOCK:
                    project=get_project();achievements=project.get('achievements',{})
                    if not isinstance(achievements,dict):achievements={}
                    achievements['preservationist']=({'unlocked':True,'unlocked_at':datetime.now(timezone.utc).isoformat()}
                        if obj['action']=='unlock' else {'unlocked':False})
                    project['achievements']=achievements
                    DATA.write_text(json.dumps(project,ensure_ascii=False,indent=2),encoding='utf-8')
                return self.reply(200,json.dumps(achievements['preservationist']).encode(),'application/json')
            except (ValueError,TypeError,KeyError) as e:
                return self.reply(400,json.dumps({'error':str(e)}).encode(),'application/json')
        if path in ('/api/archive/analyze','/api/archive/start','/api/archive/cancel'):
            if not get_project().get('achievements',{}).get('preservationist',{}).get('unlocked'):
                return self.reply(404,b'Workspace unavailable','text/plain')
            if self.client_address[0] not in ('127.0.0.1','::1'):return self.reply(403,b'Archive controls are local-only','text/plain')
            try:
                size=int(self.headers.get('Content-Length','0'))
                if size<2 or size>10000:raise ValueError('Invalid request size')
                data=json.loads(self.rfile.read(size))
                if path=='/api/archive/analyze':result=archive_forge.analyze(data.get('url',''),tool('yt-dlp'))
                elif path=='/api/archive/start':result={'id':archive_forge.begin(data.get('url',''),data.get('destination',''),tool('yt-dlp'))}
                else:result={'cancelled':archive_forge.cancel(data.get('id',''))}
                return self.reply(200,json.dumps(result).encode(),'application/json')
            except Exception as exc:
                diagnostic('archive_error',error=str(exc)[:250])
                return self.reply(400,json.dumps({'error':str(exc)[-300:]}).encode(),'application/json')
        if path=='/api/encoder':
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=256:raise ValueError('Invalid request size')
                mode=json.loads(self.rfile.read(size)).get('mode')
                if mode not in ('auto','qsv','software'):raise ValueError('Invalid encoder mode')
                with LOCK:ENCODER_PREF.write_text(json.dumps({'mode':mode}),encoding='utf-8')
                diagnostic('encoder_setting_changed',mode=mode)
                return self.reply(200,json.dumps({'mode':mode,'restart_stream_required':True}).encode(),'application/json')
            except (ValueError,TypeError,AttributeError) as exc:return self.reply(400,str(exc).encode(),'text/plain')
        if path=='/api/reshuffle':
            try:
                size=int(self.headers.get('Content-Length','0'))
                if size<1 or size>10000:raise ValueError('Invalid request')
                sid=json.loads(self.rfile.read(size)).get('id')
                source=next((x for x in get_project()['sources'] if isinstance(x,dict) and x.get('id')==sid),None)
                if not source or source_type(source.get('url',''))!='playlist':raise ValueError('Playlist source not found')
                # Stop active channel sessions before clearing saved rotation.
                with HLS_LOCK:
                    for cid,session in HLS_SESSIONS.items():
                        if channel_source(cid) and channel_source(cid).get('id')==sid:
                            session['stop'].set();stop_session(session)
                rotation_reshuffle(source)
                return self.reply(200,b'{"ok":true}','application/json')
            except (ValueError,TypeError,AttributeError) as exc:return self.reply(400,str(exc).encode(),'text/plain')
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
            ach=obj.get('achievements',{})
            if not isinstance(ach,dict):ach={}
            preservation=ach.get('preservationist',{})
            if not isinstance(preservation,dict):preservation={}
            payload=json.dumps({'sources':obj['sources'],'channels':obj['channels'],
                'achievements':{'preservationist':{'unlocked':preservation.get('unlocked') is True,
                'unlocked_at':str(preservation.get('unlocked_at',''))[:80]}}},ensure_ascii=False,indent=2)
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
    ForgeHTTPServer(('0.0.0.0',PORT),Handler).serve_forever()
