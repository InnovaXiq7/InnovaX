import os
import uuid
import shutil
import subprocess
import threading
import asyncio
import re
import gc
import requests
from pathlib import Path

from flask import Flask, request, jsonify, send_from_directory
import edge_tts


# ============================================================
# APP
# ============================================================

app = Flask(__name__)


# ============================================================
# DIRECTORIOS
# ============================================================

BASE = Path("/tmp/innovax")
BASE.mkdir(parents=True, exist_ok=True)

AUDIO_DIR = BASE / "audio"
AUDIO_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# JOBS
# ============================================================

JOBS = {}
RENDER_LOCK = threading.Lock()


# ============================================================
# PUBLIC URL
# ============================================================

PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")


def public_url(path):
    base = PUBLIC_BASE_URL
    if not base:
        hostname = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
        if hostname:
            base = f"https://{hostname}"
        else:
            base = request.host_url.rstrip("/")
    return f"{base}{path}"


# ============================================================
# UTILIDADES
# ============================================================

def safe_float(value, default=7.0):
    try:
        value = float(value)
        return value if value > 0 else default
    except Exception:
        return default


def format_ass_time(seconds):
    seconds = max(0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    whole = int(secs)
    centiseconds = int(round((secs - whole) * 100))

    if centiseconds >= 100:
        whole += 1
        centiseconds = 0

    if whole >= 60:
        minutes += whole // 60
        whole %= 60

    if minutes >= 60:
        hours += minutes // 60
        minutes %= 60

    return f"{hours}:{minutes:02d}:{whole:02d}.{centiseconds:02d}"


def clean_text(text):
    text = str(text or "")
    text = text.replace("\n", " ").replace("\r", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def get_audio_duration(file_path):
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(file_path)
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode == 0:
            return float(res.stdout.strip())
    except Exception:
        pass
    return None


def clean_job(job_id):
    job_dir = BASE / job_id
    if job_dir.exists():
        shutil.rmtree(job_dir, ignore_errors=True)


def download_file(url, dest_path):
    headers = {"User-Agent": "Mozilla/5.0"}
    with requests.get(url, headers=headers, stream=True, timeout=30) as r:
        r.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)


def download_audio(url, output_file):
    temp_download = output_file.with_suffix(".raw")
    download_file(url, temp_download)
    
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-threads", "1",
            "-i", str(temp_download), "-vn",
            "-c:a", "aac", "-b:a", "128k",
            str(output_file)
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True
    )
    if temp_download.exists():
        temp_download.unlink()
        
    if result.returncode != 0:
        raise RuntimeError("Error convirtiendo audio descargado:\n" + result.stderr[-2000:])


async def generate_tts_async(text, voice, output_file):
    communicate = edge_tts.Communicate(
        text=text, voice=voice, rate="+0%", volume="+0%", pitch="+0Hz"
    )
    await communicate.save(str(output_file))


def generate_tts(text, voice, output_file):
    asyncio.run(generate_tts_async(text, voice, output_file))


def make_caption_chunks(text):
    text = clean_text(text)
    if not text:
        return []
    words = text.split()
    if not words:
        return []

    chunks = []
    current = []

    for word in words:
        current.append(word)
        if len(current) >= 2:
            chunks.append(" ".join(current))
            current = []

    if current:
        chunks.append(" ".join(current))

    return chunks


def create_ass_subtitles(text, duration, output_file):
    text = clean_text(text)
    if not text:
        return False

    chunks = make_caption_chunks(text)
    if not chunks:
        return False

    weights = [max(1, len(re.sub(r"\s+", "", chunk))) for chunk in chunks]
    total_weight = sum(weights)

    ass = [
        "[Script Info]\n",
        "ScriptType: v4.00+\n",
        "PlayResX: 720\n",
        "PlayResY: 1280\n",
        "ScaledBorderAndShadow: yes\n",
        "WrapStyle: 0\n",
        "Collisions: Normal\n\n",
        "[V4+ Styles]\n",
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n",
        "Style: TikTok,DejaVu Sans,52,&H00FFFFFF,&H00000000,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,2,5,40,40,0,1\n\n",
        "[Events]\n",
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
    ]

    current_time = 0.0

    for index, chunk in enumerate(chunks):
        portion = weights[index] / total_weight
        chunk_duration = duration * portion
        start = current_time
        end = current_time + chunk_duration

        if (end - start) < 0.25:
            end = start + 0.25

        words = chunk.split()
        if len(words) > 1:
            formatted_text = f"{{\\c&H0000FFFF&}}{words[0]} {{\\c&H00FFFFFF&}}{' '.join(words[1:])}"
        else:
            formatted_text = f"{{\\c&H0000FFFF&}}{chunk}"

        ass_text = r"{\an5\pos(360,640)}" + formatted_text

        ass.append(
            f"Dialogue: 0,{format_ass_time(start)},{format_ass_time(min(end, duration))},TikTok,,0,0,0,,{ass_text}\n"
        )
        current_time = end

    output_file.write_text("".join(ass), encoding="utf-8")
    return True


# ============================================================
# RENDER PROCESO PRINCIPAL
# ============================================================

def run_job(job_id, scenes):
    with RENDER_LOCK:
        job_dir = BASE / job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        JOBS[job_id]["status"] = "rendering"

        try:
            inputs = []

            for i, scene in enumerate(scenes):
                src = scene.get("video_url") or scene.get("src") or scene.get("video_src")
                if not src:
                    raise ValueError(f"La escena {i + 1} no tiene video_url.")

                # Descargar clip de vídeo original localmente para evitar descargas en vuelo
                raw_video_file = job_dir / f"raw_video_{i}.mp4"
                download_file(src, raw_video_file)

                audio_url = scene.get("audio_url") or scene.get("audio_src")
                bg_music_url = scene.get("bg_music_url") or scene.get("bg_audio_url")
                subtitle = clean_text(scene.get("subtitle") or scene.get("voiceover") or scene.get("text") or "")
                voice = scene.get("voice", "es-ES-AlvaroNeural")
                duration = safe_float(scene.get("duration", 7), 7)

                scene_video = job_dir / f"scene_{i}.mp4"
                voice_audio = None
                bg_audio = None
                ass_file = job_dir / f"subtitle_{i}.ass"

                # 1. AUDIO VOZ
                if audio_url:
                    voice_audio = job_dir / f"voice_{i}.m4a"
                    download_audio(audio_url, voice_audio)
                elif subtitle:
                    voice_audio = job_dir / f"voice_{i}.mp3"
                    generate_tts(subtitle, voice, voice_audio)

                if voice_audio and voice_audio.exists():
                    audio_dur = get_audio_duration(voice_audio)
                    if audio_dur:
                        duration = audio_dur

                # 2. MUSICA
                if bg_music_url:
                    bg_audio = job_dir / f"bg_{i}.m4a"
                    download_audio(bg_music_url, bg_audio)

                # 3. SUBTITULOS
                has_subtitles = False
                if subtitle:
                    has_subtitles = create_ass_subtitles(subtitle, duration, ass_file)

                # 4. FILTROS DE VIDEO (Scale + Crop + TikTok Subtitles)
                video_filter = "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,fps=30"

                if has_subtitles:
                    ass_path = ass_file.as_posix().replace(":", r"\:").replace("'", r"\'")
                    video_filter += f",subtitles='{ass_path}'"

                cmd = ["ffmpeg", "-y", "-threads", "1", "-i", str(raw_video_file)]

                if voice_audio and voice_audio.exists() and bg_audio and bg_audio.exists():
                    cmd.extend(["-i", str(voice_audio), "-i", str(bg_audio)])
                    filter_complex = (
                        f"[0:v]{video_filter}[v];"
                        f"[1:a]volume=1.0[v_voice];"
                        f"[2:a]volume=0.20[v_bg];"
                        f"[v_voice][v_bg]amix=inputs=2:duration=first:dropout_transition=2[a]"
                    )
                    cmd.extend([
                        "-t", str(duration),
                        "-filter_complex", filter_complex,
                        "-map", "[v]", "-map", "[a]"
                    ])

                elif voice_audio and voice_audio.exists():
                    cmd.extend(["-i", str(voice_audio)])
                    cmd.extend([
                        "-t", str(duration),
                        "-vf", video_filter,
                        "-map", "0:v:0", "-map", "1:a:0"
                    ])

                elif bg_audio and bg_audio.exists():
                    cmd.extend(["-i", str(bg_audio)])
                    filter_complex = f"[0:v]{video_filter}[v];[1:a]volume=0.20[a]"
                    cmd.extend([
                        "-t", str(duration),
                        "-filter_complex", filter_complex,
                        "-map", "[v]", "-map", "[a]"
                    ])

                else:
                    cmd.extend([
                        "-t", str(duration),
                        "-vf", video_filter,
                        "-an"
                    ])

                cmd.extend([
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
                    "-threads", "1",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
                    "-movflags", "+faststart", str(scene_video)
                ])

                result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
                
                # Limpiar vídeo crudo original
                if raw_video_file.exists():
                    raw_video_file.unlink(missing_ok=True)

                if result.returncode != 0:
                    print(f"ERROR FFMPEG ESCENA {i+1}:", result.stderr)
                    raise RuntimeError(f"Error renderizando escena {i + 1}:\n" + result.stderr[-2000:])

                inputs.append(scene_video)

                if voice_audio and voice_audio.exists():
                    voice_audio.unlink(missing_ok=True)
                if bg_audio and bg_audio.exists():
                    bg_audio.unlink(missing_ok=True)
                if ass_file.exists():
                    ass_file.unlink(missing_ok=True)
                gc.collect()

            # CONCATENAR ESCENAS
            concat_file = job_dir / "concat.txt"
            concat_lines = [f"file '{v.as_posix()}'\n" for v in inputs]
            concat_file.write_text("".join(concat_lines), encoding="utf-8")

            final_file = job_dir / "final.mp4"
            concat_command = [
                "ffmpeg", "-y", "-threads", "1",
                "-f", "concat", "-safe", "0",
                "-i", str(concat_file),
                "-c", "copy", "-movflags", "+faststart",
                str(final_file)
            ]

            result = subprocess.run(concat_command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            if result.returncode != 0:
                print("ERROR FFMPEG CONCAT:", result.stderr)
                raise RuntimeError("Error concatenando escenas:\n" + result.stderr[-2000:])

            JOBS[job_id].update(
                status="succeeded",
                url=public_url(f"/files/{job_id}/final.mp4"),
                local_url=f"/files/{job_id}/final.mp4"
            )

            for video_file in inputs:
                video_file.unlink(missing_ok=True)
            concat_file.unlink(missing_ok=True)

        except Exception as e:
            JOBS[job_id].update(status="failed", error=str(e))
            clean_job(job_id)
        finally:
            gc.collect()


# ============================================================
# ENDPOINTS
# ============================================================

@app.get("/health")
def health():
    return jsonify(ok=True, service="n8n-free-ffmpeg-renderer", version=14)

@app.get("/")
def root():
    return jsonify(ok=True, service="n8n-free-ffmpeg-renderer", version=14)

@app.post("/tts")
def tts():
    try:
        data = request.get_json(silent=True) or {}
        text = clean_text(data.get("text", ""))
        voice = str(data.get("voice", "es-ES-AlvaroNeural")).strip() or "es-ES-AlvaroNeural"

        if not text:
            return jsonify(error="No text supplied."), 400

        audio_id = uuid.uuid4().hex
        filename = f"{audio_id}.mp3"
        output_file = AUDIO_DIR / filename

        generate_tts(text, voice, output_file)
        path = f"/files/audio/{filename}"

        return jsonify(id=audio_id, voice=voice, url=path, public_url=public_url(path))
    except Exception as e:
        return jsonify(error=str(e)), 500

@app.post("/render")
def render():
    data = request.get_json(silent=True) or {}
    scenes = data.get("scenes")

    if not isinstance(scenes, list) or len(scenes) < 1:
        return jsonify(error="scenes debe ser una lista válida.", id=None, status="failed"), 400

    job_id = uuid.uuid4().hex
    JOBS[job_id] = {"id": job_id, "status": "queued", "url": None, "error": None}

    normalized = []
    for index, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            continue

        normalized.append({
            "index": index,
            "video_url": scene.get("video_url") or scene.get("src") or scene.get("video_src"),
            "audio_url": scene.get("audio_url") or scene.get("audio_src"),
            "bg_music_url": scene.get("bg_music_url") or scene.get("bg_audio_url"),
            "subtitle": scene.get("subtitle") or scene.get("voiceover") or scene.get("text") or "",
            "voice": scene.get("voice", "es-ES-AlvaroNeural"),
            "duration": safe_float(scene.get("duration", 7), 7)
        })

    threading.Thread(target=run_job, args=(job_id, normalized), daemon=True).start()
    return jsonify(JOBS[job_id])

@app.get("/status/<job_id>")
def status(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify(error="No existe un render con ese ID."), 404
    return jsonify(job)

@app.get("/files/<job_id>/<filename>")
def render_file(job_id, filename):
    return send_from_directory(BASE / job_id, filename, as_attachment=False)

@app.get("/files/audio/<filename>")
def audio_file(filename):
    return send_from_directory(AUDIO_DIR, filename, as_attachment=False)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
