import gc
import os
import subprocess
import requests
from flask import Flask, jsonify, request, send_from_directory

app = Flask(__name__)

# Directorio temporal
TEMP_DIR = "/tmp/render_assets"
os.makedirs(TEMP_DIR, exist_ok=True)

# Diccionario en memoria para almacenar el estado de los renders si usas asincrónico
render_jobs = {}


def download_file_stream(url, output_path):
    """Descarga usando streams para no saturar la RAM de 512 MB."""
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(output_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)


def cleanup_temp_files():
    """Limpia la carpeta temporal y libera la RAM."""
    for file in os.listdir(TEMP_DIR):
        file_path = os.path.join(TEMP_DIR, file)
        try:
            if os.path.isfile(file_path):
                os.remove(file_path)
        except Exception as e:
            print(f"Error eliminando {file_path}: {e}")
    gc.collect()


@app.route("/render", methods=["POST"])
def render_video():
    """Endpoint sincrónico: procesa y responde cuando el render está listo."""
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
        # 1. Limpiar archivos antiguos
        cleanup_temp_files()

        # 2. Descargar vídeos
        downloaded_videos = []
        for i, url in enumerate(video_urls):
            path = os.path.join(TEMP_DIR, f"clip_{i}.mp4")
            download_file_stream(url, path)
            downloaded_videos.append(path)

        # 3. Descargar audio si existe
        if audio_url:
            audio_path = os.path.join(TEMP_DIR, "audio.mp3")
            download_file_stream(audio_url, audio_path)

        # 4. Crear lista de archivos para FFmpeg
        concat_file_path = os.path.join(TEMP_DIR, "files.txt")
        with open(concat_file_path, "w") as f:
            for vid in downloaded_videos:
                f.write(f"file '{vid}'\n")

        # 5. Comando FFmpeg ultra optimizado (512 MB RAM)
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
            "1",  # Máximo 1 hilo de CPU para no disparar la RAM
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
            print("FFmpeg Error:", error_log)
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Error al procesar FFmpeg",
                        "details": error_log[-500:],
                    }
                ),
                500,
            )

        # Devuelve la confirmación directa y la URL para descargar el archivo procesado
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


@app.route("/download/<filename>", methods=["GET"])
def download_file(filename):
    """Permite a n8n descargar el vídeo terminado directo de Render."""
    return send_from_directory(TEMP_DIR, filename, as_attachment=True)


@app.route("/status/<job_id>", methods=["GET"])
def get_status(job_id):
    """Endpoint fallback de estado para mantener compatibilidad si no usas el modo sincrónico."""
    if job_id in render_jobs:
        return jsonify(render_jobs[job_id])

    # Si la ID no existe o la app se reinició por falta de RAM:
    return (
        jsonify(
            {
                "status": "not_found",
                "error": "The resource you are requesting could not be found. El render no existe o la instancia se reinició.",
            }
        ),
        404,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
