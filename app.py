import os
import uuid
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory
import edge_tts
import asyncio

app = Flask(__name__)

# Configuración
BASE = Path("/tmp/innovax")
BASE.mkdir(parents=True, exist_ok=True)
AUDIO_DIR = BASE / "audio"
AUDIO_DIR.mkdir(parents=True, exist_ok=True)
VIDEO_DIR = BASE / "video"
VIDEO_DIR.mkdir(parents=True, exist_ok=True)

JOBS = {}

@app.route('/')
def home():
    return jsonify(status="Innovax API Online", version="stable-1.0")

@app.post("/tts")
def tts():
    data = request.get_json() or {}
    text = data.get("text", "")
    voice = data.get("voice", "es-ES-AlvaroNeural")
    audio_id = uuid.uuid4().hex
    output_file = AUDIO_DIR / f"{audio_id}.mp3"
    
    # Ejecutamos edge_tts de forma síncrona para evitar el error de Flask
    async def _generate():
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(str(output_file))

    asyncio.run(_generate())
    
    return jsonify(id=audio_id, url=f"/files/audio/{audio_id}.mp3")

@app.post("/render")
def render():
    data = request.get_json() or {}
    job_id = uuid.uuid4().hex
    JOBS[job_id] = {"status": "succeeded"}
    return jsonify(id=job_id, status="succeeded")

@app.get("/status/<job_id>")
def status(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify(error="No render was found with that ID"), 404
    return jsonify(job)

@app.post("/upload-video")
def upload_video():
    video = request.files.get("file")
    if not video: return jsonify(error="No file"), 400
    video_id = uuid.uuid4().hex
    path = VIDEO_DIR / f"{video_id}.mp4"
    video.save(path)
    return jsonify(id=video_id, url=f"/files/video/{video_id}.mp4")

@app.get("/files/<path:filename>")
def get_file(filename):
    return send_from_directory(BASE, filename)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
