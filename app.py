import os, uuid, shutil, subprocess, threading, time
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory

BASE = Path(os.environ.get("RENDER_DIR", "/tmp/n8n-renderer"))
BASE.mkdir(parents=True, exist_ok=True)
JOBS = {}

app = Flask(__name__)

def run_job(job_id, payload):
    jobdir = BASE / job_id
    jobdir.mkdir(parents=True, exist_ok=True)
    try:
        JOBS[job_id] = {"status": "rendering", "url": None, "error": None}
        scenes = payload.get("scenes", [])
        if len(scenes) != 3:
            raise ValueError("Se requieren exactamente 3 escenas.")

        inputs = []
        for i, s in enumerate(scenes):
            video_url = s.get("video_url")
            if not video_url:
                raise ValueError(f"Falta video_url en escena {i+1}")
            p = jobdir / f"scene_{i+1}.mp4"
            download(video_url, p)
            inputs.append(p)

        # Concatena clips, normaliza a 1080x1920 y aplica zoom/padding.
        # La voz/subtítulos pueden venir como archivos públicos/URLs.
        normalized = []
        for i, p in enumerate(inputs):
            out = jobdir / f"norm_{i+1}.mp4"
            cmd = [
                "ffmpeg","-y","-i",str(p),
                "-vf",
                "scale=1080:1920:force_original_aspect_ratio=increase,"
                "crop=1080:1920,setsar=1",
                "-r","30","-an",
                "-c:v","libx264","-preset","veryfast","-crf","24",
                "-pix_fmt","yuv420p",
                str(out)
            ]
            run(cmd)
            normalized.append(out)

        concat = jobdir / "concat.txt"
        concat.write_text("".join(f"file '{p.as_posix()}'\n" for p in normalized))
        joined = jobdir / "joined.mp4"
        run([
            "ffmpeg","-y","-f","concat","-safe","0","-i",str(concat),
            "-c","copy",str(joined)
        ])

        final = jobdir / "final.mp4"
        audio_urls = [s.get("audio_url") for s in scenes if s.get("audio_url")]
        if audio_urls:
            # Si hay una sola pista pública, la usa como audio global.
            ap = jobdir / "voice.mp3"
            download(audio_urls[0], ap)
            run([
                "ffmpeg","-y","-i",str(joined),"-i",str(ap),
                "-map","0:v:0","-map","1:a:0","-c:v","copy",
                "-c:a","aac","-shortest",str(final)
            ])
        else:
            shutil.copy2(joined, final)

        # URL pública relativa al servidor.
        JOBS[job_id] = {
            "status": "succeeded",
            "url": f"/files/{job_id}/final.mp4",
            "error": None
        }
    except Exception as e:
        JOBS[job_id] = {"status": "failed", "url": None, "error": str(e)}

def download(url, path):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent":"n8n-free-renderer/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r, open(path, "wb") as f:
        shutil.copyfileobj(r, f)

def run(cmd):
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stdout[-6000:])

@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "n8n-free-ffmpeg-renderer"})

@app.post("/render")
def render():
    payload = request.get_json(silent=True) or {}
    job_id = uuid.uuid4().hex
    JOBS[job_id] = {"status":"queued","url":None,"error":None}
    threading.Thread(target=run_job, args=(job_id,payload), daemon=True).start()
    return jsonify({"id": job_id, "status":"queued"}), 202

@app.get("/status/<job_id>")
def status(job_id):
    if job_id not in JOBS:
        return jsonify({"error":"job_not_found"}), 404
    return jsonify({"id":job_id, **JOBS[job_id]})

@app.get("/files/<job_id>/<filename>")
def file(job_id, filename):
    folder = BASE / job_id
    if not (folder / filename).exists():
        return jsonify({"error":"file_not_found"}), 404
    return send_from_directory(folder, filename, as_attachment=False)

if __name__ == "__main__":
    port = int(os.environ.get("PORT","8080"))
    app.run(host="0.0.0.0", port=port)
