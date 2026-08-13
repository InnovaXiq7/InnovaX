import asyncio
import gc
import os
import subprocess
import edge_tts
from flask import Flask, jsonify, request, send_from_directory

app = Flask(__name__)

# Directorio temporal para los archivos
TEMP_DIR = "/tmp/render_assets"
os.makedirs(TEMP_DIR, exist_ok=True)

# Memoria temporal para estados si los usas
render_jobs = {}


def download_file_stream(url, output_path):
    """Descarga usando streams para no saturar los 512 MB de RAM."""
    import requests

    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(output_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)


def cleanup_temp_files():
    """Limpia archivos temporales y fuerza liberación de RAM."""
    for file in os.listdir(TEMP_DIR):
        file_path = os.path.join(TEMP_DIR, file)
        try:
            if os.path.isfile(file_path):
                os.remove(file_path)
        except Exception as e:
            print(f"Error eliminando {file_path}: {e}")
    gc.collect()


# ==========================================
# ENDPOINT 1: Generación de Voz (TTS)
# URL en n8n: https://innovax.onrender.com/tts
# ==========================================
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

        host_url = request.host_url.rstrip("/")
        return jsonify(
            {
                "status": "success",
                "audio_url": f"{host_url}/download/audio.mp3",
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ==========================================
# ENDPOINT 2: Renderizado de Vídeo
# URL en n8n: https://innovax.onrender.com/render
# ==========================================
@app.route("/render", methods=["POST"])
def render_video():
    data = request.json or {}
    video_urls = data.get("video_urls", [])
    audio_url = data.get("audio_url", None)

    if not video_urls:
        return (
            jsonify({"status": "error", "message": "No video_urls provided"}),
            400,
        )

    output_filename = "output_final.mp4"
    output_render_path = os.path.join(TEMP_DIR, output_filename)

    try:
        # Limpieza inicial
        cleanup_temp_files()

        # Descarga de clips por stream
        downloaded_videos = []
        for i, url in enumerate(video_urls):
            path = os.path.join(TEMP_DIR, f"clip_{i}.mp4")
            download_file_stream(url, path)
            downloaded_videos.append(path)

        # Descarga de audio si viene especificado
        if audio_url:
            audio_path = os.path.join(TEMP_DIR, "audio.mp3")
            download_file_stream(audio_url, audio_path)

        # Crear lista de concat
        concat_file_path = os.path.join(TEMP_DIR, "files.txt")
        with open(concat_file_path, "w") as f:
            for vid in downloaded_videos:
                f.write(f"file '{vid}'\n")

        # FFmpeg Ultra Optimizado (1 Hilo de CPU para no superar 512MB RAM)
        ffmpeg_cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            concat_file_path,
            "-threads",
            "1",
            "-preset",
            "ultrafast",
            "-vf",
            "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2",
            "-c:v",
            "libx264",
            "-maxrate",
            "2.5M",
            "-bufsize",
            "5M",
            "-pix_fmt",
            "yuv420p",
            output_render_path,
        ]

        process = subprocess.run(
            ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )

        if process.returncode != 0:
            error_log = process.stderr.decode("utf-8")
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Error en FFmpeg",
                        "details": error_log[-500:],
                    }
                ),
                500,
            )

        host_url = request.host_url.rstrip("/")
        download_url = f"{host_url}/download/{output_filename}"

        return jsonify(
            {
                "status": "done",
                "message": "Render completado con éxito",
                "url": download_url,
            }
        )

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ==========================================
# ENDPOINT 3: Descarga de Archivos
# URL en n8n: https://innovax.onrender.com/download/<filename>
# ==========================================
@app.route("/download/<filename>", methods=["GET"])
def download_file(filename):
    return send_from_directory(TEMP_DIR, filename, as_attachment=True)


# ==========================================
# ENDPOINT 4: Estado (Fallback)
# URL en n8n: https://innovax.onrender.com/status/<job_id>
# ==========================================
@app.route("/status/<job_id>", methods=["GET"])
def get_status(job_id):
    if job_id in render_jobs:
        return jsonify(render_jobs[job_id])
    return (
        jsonify(
            {
                "status": "not_found",
                "error": "Render no encontrado o la instancia se reinició.",
            }
        ),
        404,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
