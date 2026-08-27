#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
import asyncio, copy, json, logging, math, os, re, shutil, signal, struct, subprocess, tempfile, time
from pathlib import Path
from fastapi import FastAPI, HTTPException, WebSocket, UploadFile, File
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel

VERSION="0.01"
APP=FastAPI(title="hAudio",version=VERSION)
log=logging.getLogger('haudio')
RUNTIME_DIR=os.environ.get('XDG_RUNTIME_DIR',f'/run/user/{os.getuid()}')
PULSE_SERVER=os.environ.get('PULSE_SERVER',f'unix:{RUNTIME_DIR}/pulse/native')
DATA=Path('/var/lib/haudio'); DATA.mkdir(parents=True, exist_ok=True)
STATE_FILE=DATA/'state.json'; REC=Path('/data/haudio/recordings'); SOUNDBOARD=Path('/data/haudio/soundboard'); SOUNDBOARD.mkdir(parents=True,exist_ok=True)
DEFAULT={"pc1_volume":70,"pc2_volume":70,"headset_volume":65,"mic_volume":50,"soundboard_volume":100,"pc1_mute":False,"pc2_mute":False,"mic_mute":False,"mic_pc1":True,"mic_pc2":True,"recording":{},"assignments":{}}
state={**DEFAULT}; procs={}; modules={}; soundboard_sink=None; levels={'pc1':-60.0,'pc2':-60.0,'microphone':-60.0,'headset':-60.0}; level_tasks={}; status_cache=None; status_cache_at=0.0; STATUS_CACHE_SECONDS=0.75; MAX_SOUND_BYTES=200*1024*1024

def run(*args):
    e=os.environ.copy(); e['XDG_RUNTIME_DIR']=RUNTIME_DIR; e['PULSE_SERVER']=PULSE_SERVER
    return subprocess.run(args,env=e,text=True,capture_output=True,timeout=5)
def pactl(*args): return run('/usr/bin/pactl',*args)
def audio_env():
    e=os.environ.copy(); e['XDG_RUNTIME_DIR']=RUNTIME_DIR; e['PULSE_SERVER']=PULSE_SERVER; return e
def endpoint_exists(kind,name):
    return bool(name) and pactl(kind,name).returncode==0
def haudio_loopback_ids(module_output):
    ids=[]
    for line in module_output.splitlines():
        parts=line.split('\t',2)
        if len(parts)>2 and parts[1]=='module-loopback' and 'HAUDIO_' in parts[2]: ids.append(parts[0])
    return ids
def valid_sound_filename(name):
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _.,'()&+\-]{0,120}\.mp3",name,re.I))
def valid_recording_filename(name):
    return bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 _.-]{0,120}\.opus',name,re.I))
def load_state():
    global state
    try: state.update(json.loads(STATE_FILE.read_text()))
    except Exception: pass
    state.setdefault('assignments',{})
def save_state():
    global status_cache
    STATE_FILE.write_text(json.dumps(state,indent=2)); STATE_FILE.chmod(0o640); status_cache=None
def audio_cards():
    """Enumerate USB audio cards and their current PipeWire node suffixes."""
    cards=[]
    try:
        out=pactl('list','cards').stdout
        for block in re.split(r'\n(?=Card #)',out):
            nm=re.search(r'^\s*Name: (alsa_card\.\S+)',block,re.M)
            product=re.search(r'device\.product\.name = "([^"]+)"',block)
            path=re.search(r'device\.bus_path = "([^"]+)"',block)
            if not (nm and product and path) or 'device.bus = "usb"' not in block: continue
            suffix=nm.group(1)[len('alsa_card.'):]
            cards.append({'id':path.group(1),'suffix':suffix,'product':product.group(1),
                          'description':(re.search(r'device.description = "([^"]+)"',block) or [None,product.group(1)])[1],
                          'source':'alsa_input.'+suffix+'.mono-fallback',
                          'sink':'alsa_output.'+suffix+'.analog-stereo'})
    except Exception:
        pass
    return cards
def selected_card(role,cards):
    assigned=state.get('assignments',{}).get(role)
    if assigned:
        for card in cards:
            if assigned in (card['id'],card['suffix']): return card
    return None
def names():
    cards=audio_cards()
    pc1=selected_card('pc1',cards) or {}
    pc2=selected_card('pc2',cards) or {}
    headset=selected_card('headset',cards) or {}
    return {
      'pc1_in':pc1.get('source'),'pc1_out':pc1.get('sink'),
      'pc2_in':pc2.get('source'),'pc2_out':pc2.get('sink'),
      'mic_in':headset.get('source'),'headset':headset.get('sink')}
def stream_index(kind,label):
    out=pactl('list',kind).stdout
    for block in re.split(r'\n(?=Sink Input #|Source Output #)',out):
        if f'application.name = "HAUDIO_{label}"' in block:
            m=re.search(r'^(?:Sink Input|Source Output) #(\d+)',block,re.M)
            if m:return m.group(1)
def ensure_soundboard_sink():
    global soundboard_sink
    for line in pactl('list','short','sinks').stdout.splitlines():
        if '\tHAUDIO_SOUNDBOARD\t' in line or line.split('\t')[1:2]==['HAUDIO_SOUNDBOARD']:
            return True
    r=pactl('load-module','module-null-sink','sink_name=HAUDIO_SOUNDBOARD','sink_properties=device.description=HAUDIO_SOUNDBOARD')
    if r.returncode==0: soundboard_sink=r.stdout.strip(); return True
    return False
def ensure_graph():
    global modules
    ensure_soundboard_sink()
    n=names()
    # Remove stale HAUDIO loopbacks so a backend restart cannot multiply audio.
    for module_id in haudio_loopback_ids(pactl('list','short','modules').stdout):
        pactl('unload-module',module_id)
    specs=[('pc1_in',n['pc1_in'],n['headset'],'PC1_IN'),('pc2_in',n['pc2_in'],n['headset'],'PC2_IN'),('mic_in',n['mic_in'],n['pc1_out'],'MIC_PC1'),('mic_in',n['mic_in'],n['pc2_out'],'MIC_PC2'),('soundboard','HAUDIO_SOUNDBOARD.monitor',n['headset'],'SOUNDBOARD_HEADSET'),('soundboard','HAUDIO_SOUNDBOARD.monitor',n['pc1_out'],'SOUNDBOARD_PC1'),('soundboard','HAUDIO_SOUNDBOARD.monitor',n['pc2_out'],'SOUNDBOARD_PC2')]
    specs=[item for item in specs if item[1] and item[2]]
    modules={}
    for key,src,sink,label in specs:
        r=pactl('load-module','module-loopback',f'source={src}',f'sink={sink}','latency_msec=10',f'source_output_properties=application.name=HAUDIO_{label}',f'sink_input_properties=application.name=HAUDIO_{label}')
        if r.returncode==0: modules[label]=r.stdout.strip()
    set_controls()
def graph_ready():
    n=names()
    required=('pc1_in','pc1_out','pc2_in','pc2_out','mic_in','headset')
    if any(not n[k] or (pactl('get-source-volume',n[k]) if k.endswith('_in') else pactl('get-sink-volume',n[k])).returncode!=0 for k in required):
        return False
    return all(stream_index(kind,label) for kind,label in (('sink-inputs','PC1_IN'),('sink-inputs','PC2_IN'),('source-outputs','MIC_PC1'),('source-outputs','MIC_PC2')))
def device_signature():
    n=names(); keys=('pc1_in','pc1_out','pc2_in','pc2_out','mic_in','headset')
    return tuple((k,bool(n[k]) and (pactl('get-source-volume',n[k]) if k.endswith('_in') else pactl('get-sink-volume',n[k])).returncode==0) for k in keys)
async def device_monitor():
    last_signature=None
    while True:
        try:
            signature=device_signature()
            if signature != last_signature:
                ensure_graph()
                last_signature=signature
            elif all(available for _,available in signature) and not graph_ready():
                ensure_graph()
        except Exception:
            pass
        await asyncio.sleep(3)
def set_controls():
    for label,key in [('PC1_IN','pc1'),('PC2_IN','pc2')]:
        idx=stream_index('sink-inputs',label)
        if idx:
            pactl('set-sink-input-volume',idx,f'{state[key+"_volume"]}%'); pactl('set-sink-input-mute',idx,'1' if state[key+'_mute'] else '0')
    n=names()
    if n['headset']: pactl('set-sink-volume',n['headset'],f'{state["headset_volume"]}%')
    if n['mic_in']: pactl('set-source-volume',n['mic_in'],f'{state["mic_volume"]}%')
    pactl('set-sink-volume','HAUDIO_SOUNDBOARD',f'{state["soundboard_volume"]}%')
    for label,key in [('MIC_PC1','mic_pc1'),('MIC_PC2','mic_pc2')]:
        idx=stream_index('source-outputs',label)
        if idx: pactl('set-source-output-mute',idx,'1' if state['mic_mute'] or not state[key] else '0')
    for label,key in [('SOUNDBOARD_PC1','pc1_mute'),('SOUNDBOARD_PC2','pc2_mute')]:
        idx=stream_index('sink-inputs',label)
        if idx: pactl('set-sink-input-mute',idx,'1' if state[key] else '0')
async def meter_source(key,source):
    while True:
        proc=None
        try:
            e=audio_env()
            proc=await asyncio.create_subprocess_exec('/usr/bin/pw-cat','--record','--target',source,'--rate','8000','--channels','1','--format','s16','--latency','100ms','-',env=e,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
            while True:
                data=await proc.stdout.read(3200)
                if not data: break
                count=len(data)//2
                if count:
                    vals=struct.unpack('<%dh'%count,data[:count*2]); rms=math.sqrt(sum(v*v for v in vals)/count)/32768.0
                    db=20*math.log10(max(rms,1e-5)); levels[key]=round(max(-60.0,min(0.0,db)),1)
        except Exception:
            pass
        finally:
            if proc and proc.returncode is None:
                proc.terminate()
                try: await proc.wait()
                except Exception: pass
        levels[key]=-60.0
        await asyncio.sleep(1)
async def level_monitor():
    current={}
    while True:
        try:
            cards=audio_cards()
            pc1=selected_card('pc1',cards); pc2=selected_card('pc2',cards); headset=selected_card('headset',cards)
            wanted={'pc1':pc1['source'] if pc1 else None,'pc2':pc2['source'] if pc2 else None,'microphone':headset['source'] if headset else None,'headset':(headset['sink']+'.monitor') if headset else None}
            for key,source in wanted.items():
                if current.get(key)!=source:
                    old=level_tasks.get(key)
                    if old: old.cancel()
                    if source:
                        level_tasks[key]=asyncio.create_task(meter_source(key,source))
                    else:
                        level_tasks.pop(key,None); levels[key]=-60.0
                    current[key]=source
        except Exception:
            pass
        await asyncio.sleep(3)
def soundboard_path(name):
    p=(SOUNDBOARD/name).resolve()
    if SOUNDBOARD.resolve() not in p.parents or not p.is_file() or p.suffix.lower()!='.mp3': raise HTTPException(404,'soundboard file not found')
    return p
def soundboard_files():
    return [{'name':p.name,'size':p.stat().st_size,'modified':p.stat().st_mtime} for p in sorted(SOUNDBOARD.glob('*.mp3'),key=lambda x:x.name.lower())]
def play_soundboard(name):
    p=soundboard_path(name)
    old=procs.get('soundboard')
    if old and old.poll() is None:
        old.terminate()
        try: old.wait(timeout=2)
        except subprocess.TimeoutExpired: old.kill(); old.wait()
    procs['soundboard']=subprocess.Popen(['/usr/bin/ffmpeg','-hide_banner','-loglevel','error','-re','-i',str(p),'-vn','-af','apad=pad_dur=3','-ac','2','-ar','48000','-flush_packets','1','-f','pulse','-buffer_duration','3000','-device','HAUDIO_SOUNDBOARD','-'],env=audio_env(),stdout=subprocess.DEVNULL,stderr=None)
    state['soundboard_playing']=name; save_state()
def stop_soundboard():
    p=procs.pop('soundboard',None)
    if p and p.poll() is None:
        p.terminate()
        try: p.wait(timeout=2)
        except subprocess.TimeoutExpired: p.kill(); p.wait()
    state['soundboard_playing']=''; save_state()
def start_recording(ch):
    if ch in procs and procs[ch].poll() is None:return
    if ch != 'session': raise HTTPException(400,'unknown channel')
    n=names()
    if not n['headset'] or not n['mic_in']: raise HTTPException(409,'headset and microphone must be assigned before recording')
    d=REC/time.strftime('%Y-%m-%d'); d.mkdir(parents=True,exist_ok=True); fn=d/(time.strftime('%Y-%m-%d_%H-%M-%S_headset-session.opus'))
    e=audio_env()
    filt='[0:a]aresample=48000[a];[1:a]aresample=48000,volume=0.70,pan=stereo|c0=c0|c1=c0[b];[a][b]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0,alimiter=limit=0.95[out]'
    args=['/usr/bin/ffmpeg','-hide_banner','-loglevel','error','-thread_queue_size','512','-f','pulse','-i',n['headset']+'.monitor','-thread_queue_size','512','-f','pulse','-i',n['mic_in'],'-filter_complex',filt,'-map','[out]','-ac','2','-ar','48000','-c:a','libopus','-b:a','128k','-f','segment','-segment_time','3600',str(fn.with_suffix('.%03d.opus'))]
    procs[ch]=subprocess.Popen(args,env=e,stdout=subprocess.DEVNULL,stderr=None)
    state['recording'][ch]=True; save_state()
def stop_recording(ch):
    p=procs.pop(ch,None)
    if p and p.poll() is None:
        p.terminate()
        try: p.wait(timeout=2)
        except subprocess.TimeoutExpired: p.kill(); p.wait()
    state['recording'][ch]=False; save_state()
async def process_monitor():
    while True:
        for key,proc in list(procs.items()):
            if proc.poll() is not None:
                procs.pop(key,None)
                if key=='session': state.setdefault('recording',{})[key]=False
                if key=='soundboard': state['soundboard_playing']=''
                save_state(); log.error('%s process exited with code %s',key,proc.returncode)
        await asyncio.sleep(1)
class Volume(BaseModel): value:int
class Mute(BaseModel): value:bool
class RenameRequest(BaseModel): name:str
class Assignment(BaseModel): role:str; card_id:str
@APP.on_event('startup')
async def startup():
    load_state(); resume_recording=bool(state.get('recording',{}).get('session')); state['recording']={}; save_state(); await asyncio.sleep(2); ensure_graph();
    if resume_recording:
        try: start_recording('session')
        except Exception: log.exception('Unable to resume recording after startup')
    asyncio.create_task(device_monitor()); asyncio.create_task(level_monitor()); asyncio.create_task(process_monitor())
@APP.get('/api/devices')
def devices():
    assigned=state.get('assignments',{})
    cards=audio_cards(); effective={role:(selected_card(role,cards) or {}).get('id','') for role in ('pc1','pc2','headset')}
    return {'cards':[{'id':c['id'],'product':c['product'],'description':c['description'],'bus_path':c['id'],
                      'roles':[role for role,value in assigned.items() if value in (c['id'],c['suffix'])]}
                     for c in cards], 'assignments':assigned, 'selected':effective}
@APP.post('/api/devices/assign')
def assign_device(req:Assignment):
    if req.role not in ('pc1','pc2','headset'): raise HTTPException(400,'invalid role')
    if req.card_id and not any(c['id']==req.card_id or c['suffix']==req.card_id for c in audio_cards()):
        raise HTTPException(404,'audio card not found')
    state.setdefault('assignments',{})[req.role]=req.card_id; save_state()
    try: ensure_graph()
    except Exception: pass
    return devices()
@APP.get('/api/status')
def status():
    global status_cache,status_cache_at
    now=time.monotonic()
    if status_cache is not None and now-status_cache_at<STATUS_CACHE_SECONDS: return copy.deepcopy(status_cache)
    n=names();
    mem=Path('/proc/meminfo').read_text(); total=int(re.search(r'MemTotal:\s+(\d+)',mem).group(1)); avail=int(re.search(r'MemAvailable:\s+(\d+)',mem).group(1))
    temp=Path('/sys/class/thermal/thermal_zone0/temp').read_text().strip()
    result={'name':'hAudio','version':VERSION,'online':True,
      'pc1':{'connected':endpoint_exists('get-source-volume',n['pc1_in']),'volume':state['pc1_volume'],'mute':state['pc1_mute']},
      'pc2':{'connected':endpoint_exists('get-source-volume',n['pc2_in']),'volume':state['pc2_volume'],'mute':state['pc2_mute']},
      'microphone':{'connected':endpoint_exists('get-source-volume',n['mic_in']),'volume':state['mic_volume'],'mute':state['mic_mute'],'route_pc1':state['mic_pc1'],'route_pc2':state['mic_pc2']},
      'headset':{'connected':endpoint_exists('get-sink-volume',n['headset']),'volume':state['headset_volume']},
      'recording':{'session': bool(procs.get('session') and procs['session'].poll() is None)},
      'soundboard':{'playing':state.get('soundboard_playing',''),'active':bool(procs.get('soundboard') and procs['soundboard'].poll() is None),'volume':state['soundboard_volume']},
      'levels':levels,
      'system':{'pipewire':Path('/usr/bin/wpctl').exists(),'disk_free_gb':round(shutil.disk_usage(REC.parent).free/1e9,1),'cpu_load':round(os.getloadavg()[0],2),'ram_used_percent':round((1-avail/total)*100,1),'temperature_c':round(int(temp)/1000,1),'uptime_seconds':round(float(Path('/proc/uptime').read_text().split()[0]))},
      'devices':devices()}
    status_cache=copy.deepcopy(result); status_cache_at=now; return result
@APP.post('/api/{target}/volume')
def volume(target: str,v:Volume):
    if target not in ('pc1','pc2','headset','mic','soundboard') or not 0<=v.value<=100: raise HTTPException(400,'invalid volume')
    state[target+'_volume']=v.value; set_controls(); save_state(); return status()
@APP.post('/api/{target}/mute')
def mute(target:str,m:Mute):
    if target not in ('pc1','pc2'): raise HTTPException(400,'invalid target')
    state[target+'_mute']=m.value; set_controls(); save_state(); return status()
@APP.post('/api/mic/mute')
def micmute(m:Mute): state['mic_mute']=m.value; set_controls(); save_state(); return status()
@APP.post('/api/mic/volume')
def micvolume(v:Volume):
    if not 0<=v.value<=100: raise HTTPException(400,'invalid volume')
    state['mic_volume']=v.value; set_controls(); save_state(); return status()
@APP.post('/api/mic/route/{pc}')
def route(pc:str,m:Mute):
    if pc not in ('pc1','pc2'): raise HTTPException(400,'invalid pc')
    state['mic_'+pc]=m.value; set_controls(); save_state(); return status()
@APP.get('/api/soundboard')
def soundboard():
    current=state.get('soundboard_playing','')
    p=procs.get('soundboard')
    if p and p.poll() is not None:
        state['soundboard_playing']=''; save_state(); current=''
    return {'files':soundboard_files(),'playing':current,'volume':state['soundboard_volume']}
@APP.post('/api/soundboard/upload')
async def upload_sound(file:UploadFile=File(...)):
    original=Path(file.filename or '').name
    if not valid_sound_filename(original): raise HTTPException(400,'only .mp3 files with a safe filename are allowed')
    target=SOUNDBOARD/original; temporary=None; total=0
    try:
        with tempfile.NamedTemporaryFile(dir=SOUNDBOARD,prefix='.upload-',suffix='.tmp',delete=False) as out:
            temporary=Path(out.name)
            while True:
                chunk=await file.read(1024*1024)
                if not chunk: break
                total+=len(chunk)
                if total>MAX_SOUND_BYTES: raise HTTPException(400,'file larger than 200 MB')
                out.write(chunk)
        if total==0: raise HTTPException(400,'file is empty')
        os.replace(temporary,target); temporary=None
    finally:
        if temporary: temporary.unlink(missing_ok=True)
    return soundboard()
@APP.post('/api/soundboard/{name}/play')
def play_sound(name:str):
    play_soundboard(name); return soundboard()
@APP.post('/api/soundboard/stop')
def stop_sound():
    stop_soundboard(); return soundboard()
@APP.get('/api/soundboard/{name}')
def download_sound(name:str): return FileResponse(soundboard_path(name),media_type='audio/mpeg',filename=soundboard_path(name).name)
@APP.post('/api/soundboard/{name}/rename')
def rename_sound(name:str,req:RenameRequest):
    p=soundboard_path(name); new=req.name.strip()
    if not valid_sound_filename(new): raise HTTPException(400,'invalid filename')
    target=p.with_name(new)
    if target.exists() and target!=p: raise HTTPException(409,'file exists')
    p.rename(target)
    if state.get('soundboard_playing')==name: state['soundboard_playing']=new; save_state()
    return soundboard()
@APP.delete('/api/soundboard/{name}')
def delete_sound(name:str):
    if state.get('soundboard_playing')==name: stop_soundboard()
    soundboard_path(name).unlink(); return soundboard()
@APP.post('/api/recording/{channel}/{action}')
def recording(channel:str,action:str):
    if action not in ('start','stop'): raise HTTPException(400,'invalid recording action')
    (start_recording if action=='start' else stop_recording)(channel); return status()
@APP.post('/api/recording/{action}-all')
def recording_all(action:str):
    (start_recording if action=='start' else stop_recording)('session')
    return status()
@APP.post('/api/recording/toggle')
def recording_toggle():
    if procs.get('session') and procs['session'].poll() is None: stop_recording('session')
    else: start_recording('session')
    return status()
def recording_path(rel):
    try: p=(REC/rel).resolve()
    except Exception: raise HTTPException(400,'invalid path')
    if REC.resolve() not in p.parents or not p.is_file() or p.suffix.lower()!='.opus': raise HTTPException(404,'recording not found')
    return p
@APP.get('/api/recordings')
def recordings():
    return [{'path':str(p.relative_to(REC)),'name':p.name,'size':p.stat().st_size,'modified':p.stat().st_mtime} for p in sorted(REC.rglob('*.opus'),key=lambda x:x.stat().st_mtime,reverse=True)]
@APP.get('/api/recordings/{rel:path}')
def download_recording(rel:str): return FileResponse(recording_path(rel),media_type='audio/ogg',filename=recording_path(rel).name)
@APP.post('/api/recordings/{rel:path}/rename')
def rename_recording(rel:str,req:RenameRequest):
    p=recording_path(rel); name=req.name.strip()
    if not valid_recording_filename(name): raise HTTPException(400,'invalid filename')
    target=p.with_name(name)
    if target.exists() and target!=p: raise HTTPException(409,'file exists')
    p.rename(target); return recordings()
@APP.delete('/api/recordings/{rel:path}')
def delete_recording(rel:str):
    recording_path(rel).unlink(); return recordings()
@APP.websocket('/ws')
async def ws(sock:WebSocket):
    await sock.accept()
    try:
        while True: await sock.send_json(status()); await asyncio.sleep(1)
    except Exception:
        return
HTML='''<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><title>hAudio 0.01</title><style>
body{font:16px system-ui;background:#0b1220;color:#eef2ff;max-width:1100px;margin:auto;padding:18px}h1{display:flex;justify-content:space-between;align-items:center;margin:0 0 18px}.online{color:#34d399;font-size:16px}.layout-row{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin-bottom:14px}.card{background:#172235;border:1px solid #263750;border-radius:14px;padding:18px;min-width:0}h2{margin:0 0 12px;font-size:19px}.state{font-weight:700;color:#34d399}.off{color:#fb7185}.row{display:flex;justify-content:space-between;align-items:center;gap:12px;margin:10px 0}label{display:flex;flex:1;flex-direction:column;gap:6px;color:#cbd5e1;font-size:14px}select{width:100%;padding:10px;border:1px solid #40536e;border-radius:8px;background:#0f1a2b;color:#eef2ff}input{width:100%;accent-color:#38bdf8}button{padding:10px 13px;margin:4px 4px 4px 0;border:0;border-radius:8px;background:#334155;color:white;font-weight:600}button:hover{background:#475569}.on{background:#047857}.danger{background:#991b1b}.meter{height:9px;background:#0ea5e9;border-radius:5px;margin:14px 0}.small{color:#9fb0c7;font-size:13px}.record{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #2b3b55;padding:9px 0}.file-record{display:block}.file-actions{margin-top:8px}.rec-on{color:#f87171;font-weight:800}.rec-off{color:#94a3b8}.system{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:8px}.metric{background:#111a2b;padding:10px;border-radius:8px}.value{display:block;font-size:19px;font-weight:700;margin-top:3px}.system-footer{border-top:1px solid #263750;margin-top:20px;padding-top:14px}.system-footer h2{font-size:15px;color:#9fb0c7}@media(max-width:700px){.layout-row{grid-template-columns:1fr}.row{flex-wrap:wrap}label{min-width:100%}.system{grid-template-columns:repeat(2,minmax(0,1fr))}}</style>
<style>.meter{height:5px;background:#020617;border:1px solid #334155;border-radius:3px;margin:14px 0}.meter i{background:#22c55e!important}#soundboard-content .file-record{display:flex;align-items:center;justify-content:space-between;gap:8px}#soundboard-content .file-actions{margin-top:0;white-space:nowrap}#soundboard-content .file-actions button{min-width:38px;padding:8px 10px;font-size:17px}@media(max-width:700px){#soundboard-content .file-record{align-items:flex-start;flex-wrap:wrap}#soundboard-content .file-actions{width:100%}}</style><h1>hAudio <span class="online">● ONLINE</span></h1><main id="app"></main><section class="card" id="soundboard-panel"><h2>SOUNDBOARD</h2><div id="soundboard-content"></div></section><script>
let s={},files=[],soundFiles=[],volumeTimers={},soundboardBusy=false,soundboardBusyLabel='';
const app=document.getElementById('app');
document.head.insertAdjacentHTML('beforeend','<style>.online{margin-left:10px}.soundboard-status{min-height:1.3em;color:#fbbf24;font-weight:600}input[type=range]{display:block;min-height:28px;cursor:pointer}.card{overflow:hidden}.row>span,.row>label{min-width:0;overflow-wrap:anywhere}@media(max-width:700px){body{padding:12px}.card{padding:14px}.layout-row{gap:10px}.row{align-items:stretch}#soundboard-content .file-record{align-items:flex-start;flex-wrap:wrap}#soundboard-content .file-record>div:first-child{width:100%;overflow-wrap:anywhere}#soundboard-content .file-actions{width:100%;display:flex;gap:6px;flex-wrap:wrap}#soundboard-content .file-actions a{display:inline-flex}#soundboard-content .file-actions button{margin:0}.system{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:420px){h1{font-size:24px}.online{font-size:13px}.system{grid-template-columns:1fr}.row{gap:8px}}</style>');
document.querySelector('h1').insertAdjacentHTML('beforeend','<small style="display:block;margin-left:auto;margin-right:18px;font-size:12px;font-weight:500;color:#9fb0c7">Author: Peter Grunert · <a href="https://www.bk99.de" target="_blank" rel="noopener" style="color:#60a5fa">bk99.de</a></small>');
function rangeIsFocused(){let e=document.activeElement;return e&&e.tagName==='INPUT'&&e.type==='range'}
async function api(u,o){let keepRange=rangeIsFocused();let res=await fetch('/api/'+u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(o||{})});if(res.ok){s=await res.json();if(!keepRange)draw()}if(u.startsWith('recording/'))loadFiles()}
function setVolume(k,v){let el=document.getElementById(k+'-volume');if(el)el.textContent=v+'%';clearTimeout(volumeTimers[k]);volumeTimers[k]=setTimeout(()=>api(k+'/volume',{value:v}),180)}
function setMicVolume(v){let el=document.getElementById('microphone-volume');if(el)el.textContent=v+'%';clearTimeout(volumeTimers.microphone);volumeTimers.microphone=setTimeout(()=>api('mic/volume',{value:v}),180)}
function setSoundboardVolume(v){let el=document.getElementById('soundboard-volume');if(el)el.textContent=v+'%';clearTimeout(volumeTimers.soundboard);volumeTimers.soundboard=setTimeout(()=>api('soundboard/volume',{value:v}),180)}
function meterWidth(k){let db=(s.levels||{})[k];return Math.max(0,Math.min(100,((db==null?-60:db)+60)*100/60))}
async function assignDevice(role,id){let res=await fetch('/api/devices/assign',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({role,card_id:id})});if(res.ok){let d=await res.json();s.devices=d;draw()}}
async function loadFiles(){try{let a=await fetch('/api/recordings'),b=await fetch('/api/soundboard');files=await a.json();let sb=await b.json();soundFiles=sb.files||[];s.soundboard=sb;draw();drawSoundboard()}catch(e){}}
function drawSoundboard(){let e=document.getElementById('soundboard-content');if(!e)return;e.innerHTML=`<div class=row><label>MP3 hochladen<input type=file accept="audio/mpeg,.mp3" onchange="soundUpload(this)"></label><button onclick="soundAction('stop')" class=danger>STOPP</button></div><p class=small>Wiedergabe an Headset und alle nicht stummgeschalteten Computer-Ausgänge.</p>${soundFiles.length?soundFiles.map(f=>`<div class="record file-record"><div>${f.name}<small class=small> ${(f.size/1048576).toFixed(1)} MB</small></div><div class=file-actions><button class=on onclick="soundAction('${encodeURIComponent(f.name)}/play')">ABSPIELEN</button><a href="/api/soundboard/${encodeURIComponent(f.name)}" download><button>DOWNLOAD</button></a><button onclick="soundRename(decodeURIComponent('${encodeURIComponent(f.name)}'))">UMBENENNEN</button><button class=danger onclick="soundDelete(decodeURIComponent('${encodeURIComponent(f.name)}'))">LÖSCHEN</button></div></div>`).join(''):'<p class=small>Noch keine MP3-Dateien vorhanden.</p>'}`}
const _drawSoundboard=drawSoundboard;drawSoundboard=function(){_drawSoundboard();let labels={'ABSPIELEN':['▶','Play'],'DOWNLOAD':['⇩','Download'],'UMBENENNEN':['✎','Rename'],'LÖSCHEN':['🗑','Delete']};document.querySelectorAll('#soundboard-content .file-actions button').forEach(b=>{let label=b.textContent.trim();if(labels[label]){b.textContent=labels[label][0];b.title=labels[label][1];b.setAttribute('aria-label',labels[label][1]);if(soundboardBusy&&label==='ABSPIELEN')b.disabled=true}});let e=document.getElementById('soundboard-content');if(e&&soundboardBusy){let p=document.createElement('p');p.className='soundboard-status';p.textContent=soundboardBusyLabel;e.insertBefore(p,e.firstChild)}}
const _drawSoundboardWithVolume=drawSoundboard;drawSoundboard=function(){_drawSoundboardWithVolume();let e=document.getElementById('soundboard-content');if(!e)return;let v=s.soundboard&&s.soundboard.volume!=null?s.soundboard.volume:100;let row=document.createElement('div');row.className='row';row.innerHTML='<span>Soundboard volume</span><b id="soundboard-volume">'+v+'%</b>';let slider=document.createElement('input');slider.type='range';slider.min='0';slider.max='100';slider.value=v;slider.setAttribute('aria-label','Soundboard volume');slider.oninput=()=>setSoundboardVolume(+slider.value);let first=e.firstElementChild;if(first)e.insertBefore(row,first);else e.appendChild(row);if(row.nextSibling)e.insertBefore(slider,row.nextSibling);else e.appendChild(slider)}
async function soundUpload(input){if(!input.files.length)return;let fd=new FormData();fd.append('file',input.files[0]);let r=await fetch('/api/soundboard/upload',{method:'POST',body:fd});if(r.ok){input.value='';loadFiles()}}
async function soundAction(u){soundboardBusy=true;soundboardBusyLabel=u==='stop'?'Stopping playback…':'Starting playback…';drawSoundboard();try{let r=await fetch('/api/soundboard/'+u,{method:'POST'});if(r.ok){let sb=await r.json();s.soundboard=sb}}finally{soundboardBusy=false;soundboardBusyLabel='';loadFiles()}}
async function soundRename(n){let v=prompt('New MP3 filename:',n);if(v){await fetch('/api/soundboard/'+encodeURIComponent(n)+'/rename',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:v})});loadFiles()}}
async function soundDelete(n){if(confirm('Delete this sound?')){await fetch('/api/soundboard/'+encodeURIComponent(n),{method:'DELETE'});loadFiles()}}
async function renameFile(p,n){let name=prompt('New filename (.opus):',n);if(name){await fetch('/api/recordings/'+encodeURIComponent(p)+'/rename',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})});loadFiles()}}
async function deleteFile(p){if(confirm('Delete this recording?')){await fetch('/api/recordings/'+encodeURIComponent(p),{method:'DELETE'});loadFiles()}}
function device(k,n,muteApi){let x=s[k]||{};return `<section class=card><h2>${n}</h2><div class="${x.connected?'state':'off'}">● ${x.connected?'VERBUNDEN':'GETRENNT'}</div><div class=row><span>Lautstärke</span><b id="${k}-volume">${x.volume||0}%</b></div><input type=range min=0 max=100 value=${x.volume||0} oninput="setVolume('${k}',+this.value)"><div class=meter></div>${muteApi?`<button class="${x.mute?'danger':''}" onclick="api('${k}/mute',{value:!s['${k}'].mute})">${x.mute?'STUMM GESCHALTET':'MUTE'}</button>`:''}</section>`}
function attachRoutingControls(){let d=s.devices||{cards:[],selected:{}},cards=d.cards||[],opts='<option value="">NICHT ZUGEORDNET</option>'+cards.map(c=>'<option value="'+c.id+'">'+c.product+' · '+c.bus_path+'</option>').join(''),rows=app.querySelectorAll('.layout-row');let pc=rows[0]&&rows[0].querySelectorAll('.card');['pc1','pc2'].forEach((role,i)=>{let c=pc&&pc[i];if(!c)return;let q=document.createElement('div');q.className='assignment';q.innerHTML='<label>Soundkarte<select data-role="'+role+'">'+opts+'</select></label>';c.appendChild(q);q.querySelector('select').value=d.selected[role]||'';q.querySelector('select').onchange=()=>assignDevice(role,q.querySelector('select').value)});let second=rows[1],cs=second&&second.querySelectorAll('.card');if(cs&&cs.length>1){let headset=cs[0],mic=cs[1];headset.style.gridColumn='1/-1';let sep=document.createElement('hr');headset.appendChild(sep);while(mic.firstChild)headset.appendChild(mic.firstChild);mic.remove();let mv=document.createElement('div');mv.innerHTML='<div class=row><span>Mikrofon-Lautstärke</span><b id="microphone-volume">'+(s.microphone&&s.microphone.volume||0)+'%</b></div><input type="range" min="0" max="100" value="'+(s.microphone&&s.microphone.volume||0)+'" oninput="setMicVolume(+this.value)">';headset.appendChild(mv);let q=document.createElement('div');q.className='assignment';q.innerHTML='<label>Headset und Mikrofon<select data-role="headset">'+opts+'</select></label>';headset.appendChild(q);q.querySelector('select').value=d.selected.headset||'';q.querySelector('select').onchange=()=>assignDevice('headset',q.querySelector('select').value)}
const _attachRoutingControls=attachRoutingControls;attachRoutingControls=function(){_attachRoutingControls();let meters=app.querySelectorAll('.meter');let volume=document.getElementById('microphone-volume');let micMeter=meters[3];if(volume&&micMeter){let row=volume.parentElement;let slider=row.nextElementSibling;if(slider&&slider.tagName==='INPUT'){micMeter.parentNode.insertBefore(row,micMeter);micMeter.parentNode.insertBefore(slider,micMeter)}}}
let meters=app.querySelectorAll('.meter'),meterKeys=['pc1','pc2','headset','microphone'];meterKeys.forEach((key,i)=>{let meter=meters[i];if(meter){meter.innerHTML='<i style="display:block;height:100%;border-radius:5px;background:#38bdf8;width:'+meterWidth(key)+'%"></i>';meter.title=(s.levels&&s.levels[key]!=null?s.levels[key]:'-')+' dB'}});for(let c of app.querySelectorAll('.card')){let h=c.querySelector('h2');if(h&&h.textContent==='GERÄTEZUORDNUNG')c.parentElement.remove()}}
function draw(){if(document.activeElement&&document.activeElement.tagName==='INPUT'&&document.activeElement.type==='range')return;let panel=document.getElementById('soundboard-panel');if(panel&&panel.parentElement===app)document.body.appendChild(panel);render();attachRoutingControls();panel=document.getElementById('soundboard-panel');let footer=document.querySelector('.system-footer');if(panel&&footer)footer.parentNode.insertBefore(panel,footer);drawSoundboard()}
function translateUi(){let map={'Lautstärke':'Volume','VERBUNDEN':'CONNECTED','GETRENNT':'DISCONNECTED','STUMM GESCHALTET':'MUTED','MIC STUMM':'MIC MUTED','AKTIV':'ACTIVE','AUS':'OFF','AUFNAHMEN VERWALTEN':'RECORDINGS','Headset-Ausgabe + Mikrofon in einer Datei':'Headset output + microphone in one file','AUFNAHME STOPPEN':'STOP RECORDING','AUFNAHME STARTEN':'START RECORDING','Eine segmentierte Opus-Datei · browserunabhängig':'Segmented Opus file · browser-independent','GERÄTEZUORDNUNG':'DEVICE ASSIGNMENT','Wähle die aktuell angeschlossene USB-Audiokarte für jede Funktion. Änderungen werden gespeichert und das Routing wird neu aufgebaut.':'Assign the currently connected USB audio card to each function. Changes are saved and the audio graph is rebuilt.','Soundkarte':'Audio card','Headset und Mikrofon':'Headset and microphone','NICHT ZUGEORDNET':'UNASSIGNED','MP3 hochladen':'Upload MP3','STOPP':'STOP','Wiedergabe an Headset und alle nicht stummgeschalteten Computer-Ausgänge.':'Playback to the headset and all non-muted computer outputs.','Noch keine MP3-Dateien vorhanden.':'No MP3 files yet.','Keine Aufnahmen vorhanden.':'No recordings available.','SYSTEMSTATUS':'SYSTEM STATUS','läuft':'running','FEHLER':'ERROR','Speicher frei':'Free storage','CPU-Last':'CPU load','RAM genutzt':'RAM used','Temperatur':'Temperature','Uptime':'Uptime'};let w=document.createTreeWalker(app,NodeFilter.SHOW_TEXT);let n;while(n=w.nextNode()){let t=n.nodeValue.trim();if(map[t])n.nodeValue=n.nodeValue.replace(t,map[t])}}
setTimeout(()=>{try{draw()}catch(e){}},0);function render(){let m=s.microphone||{},r=s.recording||{},z=s.system||{},d=s.devices||{cards:[],selected:{}},active=!!r.session;let choices=d.cards.map(c=>`<option value="${c.id}">${c.product} · ${c.bus_path}</option>`).join('');app.innerHTML=`<div class="layout-row">${device('pc1','COMPUTER 1',true)}${device('pc2','COMPUTER 2',true)}</div><div class="layout-row">${device('headset','HEADSET',false)}<section class=card><h2>MICROPHONE</h2><div class="${m.connected?'state':'off'}">● ${m.connected?'VERBUNDEN':'GETRENNT'}</div><div class=meter></div><button class="${m.mute?'danger':''}" onclick="api('mic/mute',{value:!s.microphone.mute})">${m.mute?'MIC STUMM':'MIC MUTE'}</button><div class=row><span>PC1</span><button class="${m.route_pc1?'on':''}" onclick="api('mic/route/pc1',{value:!s.microphone.route_pc1})">${m.route_pc1?'AKTIV':'AUS'}</button></div><div class=row><span>PC2</span><button class="${m.route_pc2?'on':''}" onclick="api('mic/route/pc2',{value:!s.microphone.route_pc2})">${m.route_pc2?'AKTIV':'AUS'}</button></div></section></div><div class="layout-row"><section class=card style="grid-column:1/-1"><h2>AUFNAHMEN VERWALTEN</h2><div class=row><span>Headset-Ausgabe + Mikrofon in einer Datei</span><button class="${active?'danger':'on'}" onclick="api('recording/toggle')">${active?'● AUFNAHME STOPPEN':'○ AUFNAHME STARTEN'}</button></div><p class=small>Eine segmentierte Opus-Datei · browserunabhängig</p>${files.length?files.map(f=>`<div class="record file-record"><div title="${f.path}">${f.name}<small class=small> ${(f.size/1048576).toFixed(1)} MB</small></div><div class=file-actions><a href="/api/recordings/${encodeURIComponent(f.path)}" download><button>DOWNLOAD</button></a><button onclick="renameFile('${f.path}','${f.name}')">UMBENENNEN</button><button class=danger onclick="deleteFile('${f.path}')">LÖSCHEN</button></div></div>`).join(''):'<p class=small>Keine Aufnahmen vorhanden.</p>'}</section></div><div class="layout-row"><section class=card style="grid-column:1/-1"><h2>GERÄTEZUORDNUNG</h2><p class=small>Wähle die aktuell angeschlossene USB-Audiokarte für jede Funktion. Änderungen werden gespeichert und das Routing wird neu aufgebaut.</p><div class=row><label>COMPUTER 1<select onchange="assignDevice('pc1',this.value)">${choices}</select></label><label>COMPUTER 2<select onchange="assignDevice('pc2',this.value)">${choices}</select></label><label>HEADSET + MICROPHONE<select onchange="assignDevice('headset',this.value)">${choices}</select></label></div></section></div><footer class=system-footer><h2>SYSTEMSTATUS</h2><div class=system><div class=metric>PipeWire<span class=value>${z.pipewire?'● läuft':'● FEHLER'}</span></div><div class=metric>Speicher frei<span class=value>${z.disk_free_gb??'-'} GB</span></div><div class=metric>CPU-Last<span class=value>${z.cpu_load??'-'}</span></div><div class=metric>RAM genutzt<span class=value>${z.ram_used_percent??'-'} %</span></div><div class=metric>Temperatur<span class=value>${z.temperature_c??'-'} °C</span></div><div class=metric>Uptime<span class=value>${z.uptime_seconds?Math.floor(z.uptime_seconds/3600)+' h':'-'}</span></div></div></footer>`;for(let role of ['pc1','pc2','headset']){let el=document.querySelector(`select[onchange="assignDevice('${role}',this.value)"]`);if(el&&d.selected[role])el.value=d.selected[role]}}let ws=new WebSocket(`ws://${location.host}/ws`);ws.onmessage=e=>{if(document.activeElement&&document.activeElement.tagName==='SELECT')return;s=JSON.parse(e.data);draw()};loadFiles();</script>'''
'''
HTML=HTML.replace('</script>','<style>.soundboard-status{min-height:1.3em;color:#fbbf24;font-weight:600}input[type=range]{display:block;min-height:28px;cursor:pointer}.card{overflow:hidden}.row>span,.row>label{min-width:0;overflow-wrap:anywhere}@media(max-width:700px){body{padding:12px}.card{padding:14px}.layout-row{gap:10px}.row{align-items:stretch}#soundboard-content .file-record{align-items:flex-start;flex-wrap:wrap}#soundboard-content .file-record>div:first-child{width:100%;overflow-wrap:anywhere}#soundboard-content .file-actions{width:100%;display:flex;gap:6px;flex-wrap:wrap}#soundboard-content .file-actions a{display:inline-flex}#soundboard-content .file-actions button{margin:0}.system{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:420px){h1{font-size:24px}.online{font-size:13px}.system{grid-template-columns:1fr}.row{gap:8px}}</style></script>',1)
HTML=HTML.replace('<style>.soundboard-status','</script><style>.soundboard-status',1)
HTML=HTML.replace('</style></script>','</style>',1)
'''
@APP.get('/')
def index(): return HTMLResponse(HTML)
