import os, uuid, shutil, subprocess, threading
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__)
BASE = Path('/tmp/innovax')
BASE.mkdir(parents=True, exist_ok=True)
JOBS = {}
RENDER_LOCK = threading.Lock()
PUBLIC_BASE = os.environ.get('PUBLIC_BASE_URL', '').rstrip('/')

def public_url(path):
    base = PUBLIC_BASE
    if not base:
        host = os.environ.get('RENDER_EXTERNAL_HOSTNAME')
        base = f'https://{host}' if host else request.host_url.rstrip('/')
    return f'{base}{path}'

def clean_job(job_id):
    p = BASE / job_id
    if p.exists(): shutil.rmtree(p, ignore_errors=True)

def download_audio(url, out):
    subprocess.run(['ffmpeg','-y','-threads','1','-i',url,'-vn','-c:a','aac','-b:a','128k',str(out)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

def run_job(job_id, scenes):
    with RENDER_LOCK:
        jobdir = BASE / job_id
        jobdir.mkdir(parents=True, exist_ok=True)
        JOBS[job_id]['status'] = 'rendering'
        try:
            inputs=[]
            for i, scene in enumerate(scenes):
                src = scene.get('src') or scene.get('video_url')
                if not src: raise ValueError(f'Scene {i+1} has no video source')
                audio_url = scene.get('audio_url')
                video_out = jobdir / f'scene_{i}.mp4'
                if audio_url:
                    audio_out = jobdir / f'audio_{i}.m4a'
                    download_audio(audio_url, audio_out)
                    subprocess.run([
                        'ffmpeg','-y','-threads','1','-i',src,'-i',str(audio_out),'-t','6',
                        '-vf','scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,fps=24',
                        '-map','0:v:0','-map','1:a:0','-c:v','libx264','-preset','ultrafast','-crf','28',
                        '-c:a','aac','-b:a','128k','-shortest','-pix_fmt','yuv420p',str(video_out)
                    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                    audio_out.unlink(missing_ok=True)
                else:
                    subprocess.run([
                        'ffmpeg','-y','-threads','1','-i',src,'-t','6',
                        '-vf','scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,fps=24',
                        '-c:v','libx264','-preset','ultrafast','-crf','28','-pix_fmt','yuv420p','-an',str(video_out)
                    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                inputs.append(video_out)
            concat=jobdir/'concat.txt'
            concat.write_text(''.join(f"file '{p.as_posix()}'\n" for p in inputs))
            final=jobdir/'final.mp4'
            subprocess.run(['ffmpeg','-y','-threads','1','-f','concat','-safe','0','-i',str(concat),'-c','copy','-movflags','+faststart',str(final)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            JOBS[job_id].update(status='succeeded', url=f'/files/{job_id}/final.mp4')
            for p in inputs: p.unlink(missing_ok=True)
            concat.unlink(missing_ok=True)
        except Exception as e:
            JOBS[job_id].update(status='failed', error=str(e))
            clean_job(job_id)

@app.get('/health')
def health(): return jsonify(ok=True, service='n8n-free-ffmpeg-renderer', version=4)

@app.post('/upload-audio')
def upload_audio():
    audio = request.files.get('file') or request.files.get('data')
    if not audio: return jsonify(error="No audio file supplied. Use multipart field 'file'."), 400
    audio_id=uuid.uuid4().hex
    audio_dir=BASE/'audio'; audio_dir.mkdir(parents=True, exist_ok=True)
    filename=f'{audio_id}.mp3'; audio.save(audio_dir/filename)
    path=f'/files/audio/{filename}'
    return jsonify(id=audio_id, url=path, public_url=public_url(path))

@app.post('/render')
def render():
    data=request.get_json(silent=True) or {}; scenes=data.get('scenes')
    if not isinstance(scenes,list) or len(scenes)!=3:
        return jsonify(error='Se requieren exactamente 3 escenas.', id=None, status='failed', url=None),400
    job_id=uuid.uuid4().hex
    JOBS[job_id]={'id':job_id,'status':'queued','url':None,'error':None}
    normalized=[]
    for scene in scenes:
        src=scene.get('video_url'); audio_url=scene.get('audio_url')
        for el in scene.get('elements',[]):
            if el.get('type')=='video' and el.get('src'): src=el['src']
            elif el.get('type')=='audio' and el.get('src'): audio_url=el['src']
        normalized.append({'src':src,'audio_url':audio_url})
    threading.Thread(target=run_job,args=(job_id,normalized),daemon=True).start()
    return jsonify(JOBS[job_id])

@app.get('/status/<job_id>')
def status(job_id):
    job=JOBS.get(job_id)
    if not job: return jsonify(error='No render was found with that ID.'),404
    return jsonify(job)

@app.get('/files/<job_id>/<filename>')
def files(job_id,filename): return send_from_directory(BASE/job_id,filename,as_attachment=False)

@app.get('/files/audio/<filename>')
def audio_file(filename): return send_from_directory(BASE/'audio',filename,as_attachment=False)

@app.get('/')
def root(): return jsonify(ok=True, service='n8n-free-ffmpeg-renderer', version=4)

if __name__=='__main__': app.run(host='0.0.0.0',port=int(os.environ.get('PORT',10000)))
