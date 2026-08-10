import os, uuid, shutil, subprocess, threading, json
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__)
BASE = Path("/tmp/innovax")
BASE.mkdir(parents=True, exist_ok=True)
JOBS = {}
LOCK = threading.Lock()
RENDER_LOCK = threading.Lock()

def clean_job(job_id):
    p = BASE / job_id
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)

def run_job(job_id, scenes):
    with RENDER_LOCK:
        jobdir = BASE / job_id
        jobdir.mkdir(parents=True, exist_ok=True)
        JOBS[job_id]["status"] = "rendering"
        try:
            inputs = []
            for i, scene in enumerate(scenes):
                src = scene.get("src") or scene.get("video_url")
                if not src:
                    raise ValueError(f"Scene {i+1} has no video source")
                out = jobdir / f"scene_{i}.mp4"
                subprocess.run([
                    "ffmpeg","-y","-threads","1","-i",src,
                    "-t","6",
                    "-vf","scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,fps=24",
                    "-c:v","libx264","-preset","ultrafast","-crf","28",
                    "-pix_fmt","yuv420p","-an",
                    str(out)
                ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                inputs.append(out)

            concat = jobdir / "concat.txt"
            concat.write_text("".join(f"file '{p.as_posix()}'\n" for p in inputs))
            final = jobdir / "final.mp4"
            subprocess.run([
                "ffmpeg","-y","-threads","1","-f","concat","-safe","0",
                "-i",str(concat),
                "-c","copy","-movflags","+faststart",str(final)
            ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

            JOBS[job_id].update(status="succeeded",
                                url=f"/files/{job_id}/final.mp4")
            for p in inputs:
                p.unlink(missing_ok=True)
            concat.unlink(missing_ok=True)
        except Exception as e:
            JOBS[job_id].update(status="failed", error=str(e))
            clean_job(job_id)

@app.get("/health")
def health():
    return jsonify(ok=True, service="n8n-free-ffmpeg-renderer", version=3)

@app.post("/render")
def render():
    data = request.get_json(silent=True) or {}
    scenes = data.get("scenes")
    if not isinstance(scenes, list) or len(scenes) != 3:
        return jsonify(error="Se requieren exactamente 3 escenas.", id=None, status="failed", url=None), 400

    job_id = uuid.uuid4().hex
    JOBS[job_id] = {"id": job_id, "status": "queued", "url": None, "error": None}
    # Accept the n8n movie-scene shape: each scene contains elements, first video element has src.
    normalized = []
    for scene in scenes:
        src = None
        for el in scene.get("elements", []):
            if el.get("type") == "video" and el.get("src"):
                src = el["src"]; break
        if not src:
            src = scene.get("video_url")
        normalized.append({"src": src})
    threading.Thread(target=run_job, args=(job_id, normalized), daemon=True).start()
    return jsonify(JOBS[job_id])

@app.get("/status/<job_id>")
def status(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify(error="No render was found with that ID."), 404
    return jsonify(job)

@app.get("/files/<job_id>/<filename>")
def files(job_id, filename):
    return send_from_directory(BASE / job_id, filename, as_attachment=False)

@app.get("/")
def root():
    return jsonify(ok=True, service="n8n-free-ffmpeg-renderer", version=3)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))