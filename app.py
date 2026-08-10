import os, uuid, shutil, subprocess, threading, urllib.request, json
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory

BASE = Path(os.environ.get("RENDER_DIR", "/tmp/n8n-renderer"))
BASE.mkdir(parents=True, exist_ok=True)
JOBS = {}
app = Flask(__name__)


def download(url, path):
    req = urllib.request.Request(url, headers={"User-Agent": "n8n-free-renderer/2.0"})
    with urllib.request.urlopen(req, timeout=180) as r, open(path, "wb") as f:
        shutil.copyfileobj(r, f)


def run(cmd):
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stdout[-8000:])


def get_video_url(scene):
    # New n8n format: scene.elements[].type=video, src=<URL>
    for el in scene.get("elements", []) or []:
        if el.get("type") == "video" and el.get("src"):
            return el["src"]
    # Also support the simpler original renderer format.
    return scene.get("video_url") or scene.get("videoUrl") or scene.get("url")


def get_voice_text(scene):
    for el in scene.get("elements", []) or []:
        if el.get("type") == "voice":
            return (el.get("text") or "").strip()
    return ""


def get_audio_url(scene):
    # Optional: if n8n later supplies an already-generated TTS file.
    if scene.get("audio_url"):
        return scene["audio_url"]
    for el in scene.get("elements", []) or []:
        if el.get("type") == "audio" and el.get("src"):
            return el["src"]
    return None


def make_ass_subtitles(texts, durations, out_path):
    """Create simple Spanish ASS subtitles, one caption per scene."""
    header = """[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\nStyle: Default,Arial,70,&H0000D7FF,&H0000D7FF,&H00101010,&H80000000,1,0,0,0,100,100,0,0,1,4,1,2,60,60,170,1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"""
    def ts(sec):
        sec = max(0.0, float(sec))
        h = int(sec // 3600); m = int((sec % 3600) // 60); s = sec % 60
        return f"{h}:{m:02d}:{s:05.2f}"
    lines = [header]
    t = 0.0
    for text, dur in zip(texts, durations):
        text = (text or "").replace("{", "\\{").replace("}", "\\}").replace("\n", " ").strip()
        end = t + max(0.5, float(dur))
        if text:
            lines.append(f"Dialogue: 0,{ts(t)},{ts(end)},Default,,0,0,0,,{text}\n")
        t = end
    out_path.write_text("".join(lines), encoding="utf-8")


def run_job(job_id, payload):
    jobdir = BASE / job_id
    jobdir.mkdir(parents=True, exist_ok=True)
    try:
        JOBS[job_id] = {"status": "rendering", "url": None, "error": None}
        movie = payload.get("movie") or payload
        scenes = movie.get("scenes", [])
        if len(scenes) != 3:
            raise ValueError(f"Se requieren exactamente 3 escenas. Recibidas: {len(scenes)}")

        inputs = []
        texts = []
        for i, scene in enumerate(scenes):
            video_url = get_video_url(scene)
            if not video_url:
                raise ValueError(f"Falta la URL del vídeo en escena {i+1}")
            p = jobdir / f"scene_{i+1}.mp4"
            download(video_url, p)
            inputs.append(p)
            texts.append(get_voice_text(scene))

        # Normalize all clips to the requested vertical canvas.
        normalized = []
        durations = []
        for i, p in enumerate(inputs):
            out = jobdir / f"norm_{i+1}.mp4"
            cmd = [
                "ffmpeg", "-y", "-i", str(p),
                "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1",
                "-r", "30", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
                "-pix_fmt", "yuv420p", str(out)
            ]
            run(cmd)
            normalized.append(out)
            probe = subprocess.run([
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(out)
            ], capture_output=True, text=True)
            durations.append(float(probe.stdout.strip() or "1"))

        concat = jobdir / "concat.txt"
        concat.write_text("".join(f"file '{p.as_posix()}'\n" for p in normalized), encoding="utf-8")
        joined = jobdir / "joined.mp4"
        run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", str(joined)])

        # Burn the voiceover text as scene subtitles. This does not generate audio;
        # it makes the current n8n payload visually complete without requiring a paid renderer.
        ass = jobdir / "subtitles.ass"
        make_ass_subtitles(texts, durations, ass)
        final = jobdir / "final.mp4"
        run([
            "ffmpeg", "-y", "-i", str(joined), "-vf", f"ass={ass}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
            "-pix_fmt", "yuv420p", "-an", str(final)
        ])

        JOBS[job_id] = {
            "status": "succeeded",
            "url": f"/files/{job_id}/final.mp4",
            "error": None,
            "audio": False,
            "note": "Vídeo renderizado con FFmpeg. El payload actual no contiene archivos de audio; el texto de voiceover se usa como subtítulo."
        }
    except Exception as e:
        JOBS[job_id] = {"status": "failed", "url": None, "error": str(e)}


@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "n8n-free-ffmpeg-renderer", "version": 2})


@app.post("/render")
def render():
    payload = request.get_json(silent=True) or {}
    job_id = uuid.uuid4().hex
    JOBS[job_id] = {"status": "queued", "url": None, "error": None}
    threading.Thread(target=run_job, args=(job_id, payload), daemon=True).start()
    return jsonify({"id": job_id, "status": "queued"}), 202


@app.get("/status/<job_id>")
def status(job_id):
    if job_id not in JOBS:
        return jsonify({"error": "job_not_found"}), 404
    return jsonify({"id": job_id, **JOBS[job_id]})


@app.get("/files/<job_id>/<filename>")
def file(job_id, filename):
    folder = BASE / job_id
    target = folder / filename
    if not target.exists():
        return jsonify({"error": "file_not_found"}), 404
    return send_from_directory(folder, filename, as_attachment=False)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
