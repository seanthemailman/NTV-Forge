"""Archive Forge: local-only playlist analysis and archival jobs."""
import json, os, re, shutil, subprocess, threading, time, urllib.parse
from pathlib import Path

LOCK=threading.RLock()
JOBS={}
MAX_ITEMS=2000

def is_playlist(url):
    try:
        p=urllib.parse.urlsplit(url)
        return p.scheme=='https' and (p.hostname or '').lower() in ('youtube.com','www.youtube.com','m.youtube.com','music.youtube.com') and bool(urllib.parse.parse_qs(p.query).get('list'))
    except (TypeError,ValueError):return False

def clean_name(value):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]','_',str(value)).strip(' .')[:100] or 'Untitled Playlist'

def analyze(url,yt_dlp):
    if not is_playlist(url):raise ValueError('Use a YouTube playlist URL containing list=')
    proc=subprocess.run([yt_dlp,'--flat-playlist','--dump-single-json','--no-warnings','--playlist-end',str(MAX_ITEMS),url],capture_output=True,text=True,timeout=180,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if proc.returncode:raise RuntimeError((proc.stderr or 'Playlist analysis failed')[-350:])
    info=json.loads(proc.stdout)
    entries=[]
    for item in info.get('entries',[]):
        if not isinstance(item,dict):continue
        seconds=item.get('duration') or 0
        try:seconds=max(0,float(seconds))
        except (ValueError,TypeError):seconds=0
        entries.append({'id':str(item.get('id') or '')[:40],'title':str(item.get('title') or 'Untitled')[:180], 'duration':round(seconds,1)})
    duration=sum(e['duration'] for e in entries)
    # Approximation only; 720p video+audio can vary greatly with codec and source.
    estimate=round(duration*2_000_000/8)
    return {'title':str(info.get('title') or 'YouTube Playlist')[:180], 'count':len(entries), 'total_seconds':round(duration), 'estimated_bytes':estimate, 'estimate_note':'Rough 2 Mbps estimate, not an exact size. Missing durations are excluded.', 'entries':entries, 'truncated':len(entries)>=MAX_ITEMS}

def status(job_id):
    with LOCK:
        item=JOBS.get(job_id)
        if not item:return None
        return {k:v for k,v in item.items() if k not in ('process','thread')}

def begin(url,destination,yt_dlp):
    if not is_playlist(url):raise ValueError('Invalid YouTube playlist URL')
    dest=Path(destination).expanduser()
    if not dest.is_absolute():raise ValueError('Destination must be an absolute local or mounted NAS path')
    if not dest.is_dir():raise ValueError('Destination folder must already exist and be accessible to Forge')
    if not os.access(dest,os.W_OK):raise ValueError('Destination folder is not writable')
    import uuid
    job_id=str(uuid.uuid4())
    with LOCK:
        if any(x['state'] in ('analyzing','downloading') for x in JOBS.values()):raise ValueError('An archive job is already running')
        JOBS[job_id]={'id':job_id,'state':'analyzing','completed':0,'total':0,'message':'Reading playlist','destination':str(dest),'started':time.strftime('%Y-%m-%d %H:%M:%S'),'cancel':False}
    threading.Thread(target=_worker,args=(job_id,url,dest,yt_dlp),daemon=True).start()
    return job_id

def cancel(job_id):
    with LOCK:
        job=JOBS.get(job_id)
        if not job:return False
        job['cancel']=True
        process=job.get('process')
    if process and process.poll() is None:process.terminate()
    return True

def _worker(job_id,url,dest,yt_dlp):
    try:
        details=analyze(url,yt_dlp)
        target=dest/clean_name(details['title'])
        target.mkdir(parents=True,exist_ok=True)
        (target/'playlist-info.json').write_text(json.dumps({'url':url,'title':details['title'],'entries':details['entries']},indent=2,ensure_ascii=False),encoding='utf-8')
        with LOCK:JOBS[job_id].update(state='downloading',total=details['count'],message='Downloading videos',destination=str(target))
        # yt-dlp maintains a persistent archive list to avoid redownloading videos.
        cmd=[yt_dlp,'--yes-playlist','--ignore-errors','--no-overwrites','--continue','--newline','--write-info-json','--write-thumbnail','--write-description','--download-archive',str(target/'downloaded-ids.txt'),'-P',str(target),'-o','%(playlist_index)04d - %(title).150B [%(id)s].%(ext)s',url]
        proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,errors='replace',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        with LOCK:JOBS[job_id]['process']=proc
        for line in proc.stdout:
            with LOCK:
                job=JOBS[job_id]
                if '[download] Destination:' in line or '[download] 100%' in line or 'has already been recorded in the archive' in line:
                    job['message']=line.strip()[-180:]
                if '[download] 100%' in line or 'has already been recorded in the archive' in line:
                    job['completed']=min(job['total'],job['completed']+1)
                stopping=job['cancel']
            if stopping:
                proc.terminate();break
        code=proc.wait()
        with LOCK:
            job=JOBS[job_id]
            job.pop('process',None)
            job['state']='cancelled' if job['cancel'] else ('complete' if code==0 else 'partial')
            job['message']='Archive job finished' if code==0 else ('Cancelled' if job['cancel'] else 'Some downloads failed; retry to resume')
    except Exception as exc:
        with LOCK:JOBS[job_id].update(state='error',message=str(exc)[-300:])
