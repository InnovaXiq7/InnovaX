import asyncio
import gc
import os
import subprocess
import edge_tts
from flask import Flask, jsonify, request, send_from_directory

app = Flask(__name__)

# Directorio temporal para almacenar los renders y recursos
TEMP_DIR = os.path.abspath("/tmp/render_assets")
os.makedirs(TEMP_DIR, exist_ok=True)

render_jobs = {}


def download_file_stream(url, output_path):
    """Descarga vídeos y audios en bloques (chunked) simulando un navegador."""
    import requests

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            " (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
    }
    with requests.get(
        url, headers=headers, stream=True, timeout=60
    ) as response:
        response.raise_for_status()
        with open(output_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)


def cleanup_temp_files(keep_audio=True):
    """Limpia la carpeta temporal para liberar espacio en disco y memoria RAM."""
    if not os.path.exists(TEMP_DIR):
        return

    for file in os.listdir(TEMP_DIR):
        if keep_audio and file == "audio.mp3":
            continue
        file_path = os.path.join(TEMP_DIR, file)
        try:
            if os.path.isfile(file_path):
                os.remove(file_path)
        except Exception as e:
            print(f"Error borrando {file_path}: {e}")
    gc.collect()


# ==========================================
# ENDPOINT 1: Generación de Voz (TTS)
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
        host_url = request.host_url.rstrip("/").replace("http://", "https://")
        return jsonify(
            {
                "status": "success",
                "audio_url": f"{host_url}/download/audio.mp3",
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ==========================================
# ENDPOINT 2: Renderizado de Vídeo (Síncrono)
# ==========================================
@app.route("/render", methods=["POST"])
def render_video():
    data = request.json or {}
    raw_video_urls = data.get("video_urls", [])
    raw_audio_url = data.get("audio_url", None)

    if not raw_video_urls:
        return (
            jsonify({"status": "error", "message": "No video_urls provided"}),
            400,
        )

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
        return (
            jsonify(
                {
                    "status": "error",
                    "message": "No valid video HTTP URLs provided",
                }
            ),
            400,
        )

    audio_url = None
    if isinstance(raw_audio_url, str):
        clean_a = raw_audio_url.lstrip("=").strip()
        if clean_a.startswith("http"):
            audio_url = clean_a

    output_filename = "output_final.mp4"
    output_render_path = os.path.join(TEMP_DIR, output_filename)

    try:
        # Limpieza previa de temporales conservando audio.mp3 si existía
        cleanup_temp_files(keep_audio=True)
        gc.collect()

        # 1. Descargar clips de vídeo
        downloaded_videos = []
        for i, url in enumerate(video_urls):
            clip_path = os.path.join(TEMP_DIR, f"clip_{i}.mp4")
            download_file_stream(url, clip_path)
            downloaded_videos.append(clip_path)

        # 2. Descargar o usar audio existente
        audio_path = None
        if audio_url:
            local_audio = os.path.join(TEMP_DIR, "audio.mp3")
            if os.path.exists(local_audio):
                audio_path = local_audio
            else:
                download_file_stream(audio_url, local_audio)
                audio_path = local_audio

        # 3. Crear lista de entrada para FFmpeg concat
        concat_file_path = os.path.join(TEMP_DIR, "files.txt")
        with open(concat_file_path, "w", encoding="utf-8") as f:
            for vid in downloaded_videos:
                f.write(f"file '{vid}'\n")

        # 4. Comando FFmpeg optimizado para bajo consumo de RAM
        ffmpeg_cmd = [
            "ffmpeg",
            "-y",
            "-thread_queue_size",
            "16",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            concat_file_path,
        ]

        if audio_path and os.path.exists(audio_path):
            ffmpeg_cmd.extend(["-i", audio_path, "-map", "0:v", "-map", "1:a"])

        ffmpeg_cmd.extend(
            [
                "-threads",
                "1",
                "-preset",
                "ultrafast",
                "-tune",
                "zerolatency",
                "-vf",
                (
                    "scale=1080:1920:force_original_aspect_ratio=decrease,"
                    "pad=1080:1920:(1080-iw)/2:(1920-ih)/2"
                ),
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                "-b:v",
                "1500k",
                "-maxrate",
                "1500k",
                "-bufsize",
                "3000k",
                "-pix_fmt",
                "yuv420p",
                "-shortest",
                output_render_path,
            ]
        )

        process = subprocess.run(
            ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )

        # Eliminar clips descargados individuales tras concatenar
        for clip in downloaded_videos:
            if os.path.exists(clip):
                try:
                    os.remove(clip)
                except Exception:
                    pass
        gc.collect()

        if process.returncode != 0:
            error_log = process.stderr.decode("utf-8")
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "FFmpeg falló durante la codificación",
                        "details": error_log[-500:],
                    }
                ),
                500,
            )

        host_url = request.host_url.rstrip("/").replace("http://", "https://")
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
# ENDPOINT 3: Descargar Archivo Producido
# ==========================================
@app.route("/download/<filename>", methods=["GET"])
def download_file(filename):
    file_path = os.path.join(TEMP_DIR, filename)
    if not os.path.exists(file_path):
        return (
            jsonify(
                {
                    "error": (
                        f"El archivo '{filename}' no existe en el disco."
                        " Puede haber sido eliminado por falta de espacio o por un fallo en el render."
                    )
                }
            ),
            404,
        )

    return send_from_directory(
        TEMP_DIR, filename, as_attachment=True, mimetype="video/mp4"
    )


# ==========================================
# ENDPOINT 4: Estado (Fallback)
# ==========================================
@app.route("/status/<job_id>", methods=["GET"])
def get_status(job_id):
    if job_id in render_jobs:
        return jsonify(render_jobs[job_id])
    return jsonify({"status": "not_found", "error": "Job no encontrado"}), 404


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
