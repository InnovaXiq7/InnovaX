import gc
import os
import subprocess
import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

# Directorio temporal para los assets
TEMP_DIR = "/tmp/render_assets"
os.makedirs(TEMP_DIR, exist_ok=True)


def download_file_stream(url, output_path):
    """Descarga un archivo en trozos (chunks) para no cargarlo en la memoria RAM."""
    with requests.get(url, stream=True, timeout=30) as r:
        r.raise_for_status()
        with open(output_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):  # Chunks de 1MB
                if chunk:
                    f.write(chunk)


def cleanup_temp_files():
    """Limpia los archivos temporales y fuerza la liberación de RAM."""
    for file in os.listdir(TEMP_DIR):
        file_path = os.path.join(TEMP_DIR, file)
        try:
            if os.path.isfile(file_path):
                os.remove(file_path)
        except Exception as e:
            print(f"Error eliminando {file_path}: {e}")

    # Forzar al Garbage Collector de Python a limpiar memoria inmediatamente
    gc.collect()


@app.route("/render", methods=["POST"])
def render_video():
    data = request.json
    # Supongamos que recibes las URLs de los vídeos/audios
    video_urls = data.get("video_urls", [])
    audio_url = data.get("audio_url", None)

    output_render_path = os.path.join(TEMP_DIR, "output_final.mp4")

    try:
        # 1. Limpieza previa por si quedaron restos
        cleanup_temp_files()

        # 2. Descargar assets usando Streams (Ahorro masivo de RAM)
        downloaded_videos = []
        for i, url in enumerate(video_urls):
            path = os.path.join(TEMP_DIR, f"clip_{i}.mp4")
            download_file_stream(url, path)
            downloaded_videos.append(path)

        if audio_url:
            audio_path = os.path.join(TEMP_DIR, "audio.mp3")
            download_file_stream(audio_url, audio_path)

        # 3. Crear archivo de lista para FFmpeg concat
        concat_file_path = os.path.join(TEMP_DIR, "files.txt")
        with open(concat_file_path, "w") as f:
            for vid in downloaded_videos:
                f.write(f"file '{vid}'\n")

        # 4. Comando FFmpeg ULTRA OPTIMIZADO PARA 512 MB RAM
        ffmpeg_cmd = [
            "ffmpeg",
            "-y",  # Sobrescribir salida
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            concat_file_path,
            # --- PARÁMETROS CRÍTICOS PARA NO CRASHAEAR EN 512MB ---
            "-threads",
            "1",  # Un solo hilo (evita picos de RAM)
            "-preset",
            "ultrafast",  # Mínimo uso de memoria para codificación
            "-vf",
            "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2",  # Redimensionar seguro a Vertical HD
            "-c:v",
            "libx264",
            "-maxrate",
            "2.5M",  # Limita la tasa de bits (evita spikes)
            "-bufsize",
            "5M",  # Búfer pequeño en RAM
            "-pix_fmt",
            "yuv420p",
            output_render_path,
        ]

        # Ejecutar FFmpeg controlando el proceso
        process = subprocess.run(
            ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )

        if process.returncode != 0:
            print("FFmpeg Error:", process.stderr.decode("utf-8"))
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "FFmpeg falló al procesar el vídeo.",
                    }
                ),
                500,
            )

        # Aquí subirías el resultado a un S3 / Cloudinary o lo devolverías
        # ...

        return jsonify(
            {"status": "success", "message": "Render completado con éxito"}
        )

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

    finally:
        # 5. Limpieza absoluta al terminar (éxito o fallo)
        cleanup_temp_files()


if __name__ == "__main__":
    # Asegurar que el servidor no corra en modo Debug pesado
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
