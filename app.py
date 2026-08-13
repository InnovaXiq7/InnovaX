import os
import subprocess
import threading
import uuid
import gc
import asyncio
import edge_tts
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__)

TEMP_DIR = os.path.abspath("/tmp/render_assets")
os.makedirs(TEMP_DIR, exist_ok=True)

# Diccionario en memoria para rastrear el estado de cada renderizado
render_jobs = {}


def download_file_stream(url, output_path):
    """Descarga de archivos por fragmentos simulando cabeceras de navegador."""
    import requests
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "*/*"
    }
    with requests.get(url, headers=headers, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(output_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)


def async_render_process(job_id, video_urls, audio_url, host_url):
    """Tarea que ejecuta el procesamiento de vídeo en segundo plano."""
    output_filename = f"output_{job_id}.mp4"
    output_render_path = os.path.join(TEMP_DIR, output_filename)

    try:
        render_jobs[job_id] = {"status": "processing", "message": "Descargando recursos..."}

        # 1. Descargar vídeos
        downloaded_videos = []
        for i, url in enumerate(video_urls):
            path = os.path.join(TEMP_DIR, f"clip_{job_id}_{i}.mp4")
            download_file_stream(url, path)
            downloaded_videos.append(path)

        # 2. Obtener audio
        audio_path = None
        if audio_url:
            local_audio = os.path.join(TEMP_DIR, "audio.mp3")
            if os.path.exists(local_audio):
                audio_path = local_audio
            else:
                audio_path = os.path.join(TEMP_DIR, f"audio_{job_id}.mp3")
                download_file_stream(audio_url, audio_path)

        # 3. Preparar concatenación
        concat_file_path = os.path.join(TEMP_DIR, f"files_{job_id}.txt")
        with open(concat_file_path, "w", encoding="utf-8") as f:
            for vid in downloaded_videos:
                f.write(f"file '{vid}'\n")

        render_jobs[job_id] = {"status": "processing", "message": "Codificando vídeo con FFmpeg..."}

        # 4. Comando FFmpeg ultrarrápido
        ffmpeg_cmd = [
            "ffmpeg", "-y",
            "-thread_queue_size", "16",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_file_path,
        ]

        if audio_path and os.path.exists(audio_path):
            ffmpeg_cmd.extend(["-i", audio_path, "-map", "0:v", "-map", "1:a"])

        ffmpeg_cmd.extend([
            "-threads", "1",
            "-preset", "ultrafast",
            "-tune", "zerolatency",
            "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(1080-iw)/2:(1920-ih)/2",
            "-c:v", "libx264",
            "-c:a", "aac",
            "-b:v", "1200k",
            "-maxrate", "1200k",
            "-bufsize", "2400k",
            "-pix_fmt", "yuv420p",
            "-shortest",
            output_render_path
        ])

        process = subprocess.run(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        # Limpieza de archivos intermedios
        for clip in downloaded_videos:
            if os.path.exists(clip):
                try: os.remove(clip)
                except Exception: pass
        if os.path.exists(concat_file_path):
            try: os.remove(concat_file_path)
            except Exception: pass
        gc.collect()

        if process.returncode != 0:
            error_log = process.stderr.decode('utf-8')
            render_jobs[job_id] = {
                "status": "error",
                "message": "Error en FFmpeg",
                "details": error_log[-300:]
            }
        else:
            download_url = f"{host_url}/download/{output_filename}"
            render_jobs[job_id] = {
                "status": "done",
                "message": "Render completado con éxito",
                "url": download_url
            }

    except Exception as e:
        render_jobs[job_id] = {"status": "error", "message": str(e)}


@app.route("/tts", methods=["POST"])
def generate_tts():
    data = request.json or {}
    text = data.get("text", "")
    voice = data.get("voice", "es-ES-AlvaroNeural")

    if not text:
        return jsonify({"error": "No text provided"}), 400

    output_audio_path = os.path.join(TEMP_DIR, "audio.mp3")

    async def _generate():
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(output_audio_path)

    try:
        asyncio.run(_generate())
        host_url = request.host_url.rstrip('/').replace("http://", "https://")
        return jsonify({
            "status": "success",
            "audio_url": f"{host_url}/download/audio.mp3"
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/render", methods=["POST"])
def render_video():
    data = request.json or {}
    raw_video_urls = data.get("video_urls", [])
    raw_audio_url = data.get("audio_url", None)

    if not raw_video_urls:
        return jsonify({"status": "error", "message": "No video_urls provided"}), 400

    if isinstance(raw_video_urls, str):
        raw_video_urls = raw_video_urls.split(",")

    video_urls = []
    if isinstance(raw_video_urls, list):
        for url in raw_video_urls:
            if isinstance(url, str):
                clean_u = url.lstrip("=").strip()
                if clean_u.startswith("http"):
                    video_urls.append(clean_u)

    if not video_urls:
        return jsonify({"status": "error", "message": "No valid video HTTP URLs provided"}), 400

    audio_url = None
    if isinstance(raw_audio_url, str):
        clean_a = raw_audio_url.lstrip("=").strip()
        if clean_a.startswith("http"):
            audio_url = clean_a

    job_id = str(uuid.uuid4())[:8]
    host_url = request.host_url.rstrip('/').replace("http://", "https://")

    # Iniciar procesamiento en un hilo independiente
    thread = threading.Thread(
        target=async_render_process,
        args=(job_id, video_urls, audio_url, host_url)
    )
    thread.start()

    # Responder INMEDIATAMENTE para evitar el 504 Gateway Timeout de Render
    return jsonify({
        "status": "processing",
        "id": job_id,
        "message": "Trabajo de renderizado iniciado"
    })


@app.route("/status/<job_id>", methods=["GET"])
def get_status(job_id):
    if job_id in render_jobs:
        return jsonify(render_jobs[job_id])
    return jsonify({"status": "not_found", "error": "El ID de renderizado no existe"}), 404


@app.route("/download/<filename>", methods=["GET"])
def download_file(filename):
    file_path = os.path.join(TEMP_DIR, filename)
    if not os.path.exists(file_path):
        return jsonify({"error": f"El archivo '{filename}' no existe o ha sido eliminado."}), 404
    return send_from_directory(TEMP_DIR, filename, as_attachment=True, mimetype="video/mp4")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
