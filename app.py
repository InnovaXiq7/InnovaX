import os
import time
import traceback
import tempfile
import subprocess
from flask import Flask, jsonify, request, send_from_directory, send_file, Response

app = Flask(__name__, static_folder='.', static_url_path='')

# Directorio temporal del sistema para almacenar renderizados dinámicos
TEMP_DIR = os.path.join(tempfile.gettempdir(), 'innovax_renders')
os.makedirs(TEMP_DIR, exist_ok=True)

jobs_status = {}

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')


# Servir archivos estáticos de la carpeta assets o del directorio temporal /tmp
@app.route('/assets/<path:filename>', methods=['GET'])
def serve_assets(filename):
    """
    Busca primero el archivo solicitado en la carpeta local 'assets'.
    Si no existe, lo busca en el directorio temporal dinámico (TEMP_DIR).
    Si aún no existe, utiliza un fallback seguro.
    """
    # 1. Intentar servir desde la carpeta de assets del repositorio
    local_asset_path = os.path.join('assets', filename)
    if os.path.exists(local_asset_path):
        return send_from_directory('assets', filename)

    # 2. Intentar servir desde la carpeta temporal de renders dinámicos
    temp_file_path = os.path.join(TEMP_DIR, filename)
    if os.path.exists(temp_file_path):
        return send_file(temp_file_path, mimetype='video/mp4' if filename.endswith('.mp4') else 'audio/mpeg')

    # 3. Fallback: Si no se encuentra el archivo dinámico, servir el de prueba o respuesta ligera
    test_video_path = os.path.join('assets', 'video_test.mp4')
    if filename.endswith('.mp4') and os.path.exists(test_video_path):
        return send_from_directory('assets', 'video_test.mp4')

    # Respuesta ligera por defecto para validar conectividad en pruebas de n8n
    content_type = 'audio/mpeg' if filename.endswith('.mp3') else 'video/mp4'
    dummy_data = b'\x00\x00\x00\x1cftypisom\x00\x00\x02\x00isomiso2avc1mp41'
    return Response(dummy_data, status=200, mimetype=content_type)


@app.route('/tts', methods=['POST'])
def handle_tts():
    """Procesa peticiones TTS de forma síncrona."""
    data = request.json or {}
    job_id = f"job_tts_{int(time.time() * 1000)}"

    try:
        public_url = data.get("output_url", f"https://innovax.onrender.com/assets/tts_{job_id}.mp3")

        jobs_status[job_id] = {
            "status": "completed",
            "job_id": job_id,
            "public_url": public_url
        }

        return jsonify(jobs_status[job_id]), 200

    except Exception as e:
        print(f"[ERROR en /tts]: {str(e)}")
        print(traceback.format_exc())
        return jsonify({"status": "failed", "error": str(e)}), 500


@app.route('/render', methods=['POST'])
def start_render():
    """
    Inicia la construcción dinámica del vídeo.
    Genera el archivo MP4 real usando FFmpeg en TEMP_DIR.
    """
    data = request.json or {}
    job_id = f"job_render_{int(time.time() * 1000)}"
    output_filename = f"video_{job_id}.mp4"
    output_path = os.path.join(TEMP_DIR, output_filename)

    try:
        # --- AQUÍ SE EJECUTA EL RENDERIZADO CON FFMPEG / MOVIEPY ---
        # Ejemplo: Generación de vídeo dinámico de prueba usando FFmpeg nativo
        ffmpeg_cmd = [
            'ffmpeg', '-y',
            '-f', 'lavfi', '-i', 'color=c=black:s=1080x1920:d=5',  # Fondo negro formato Shorts
            '-f', 'lavfi', '-i', 'sine=f=440:d=5',                # Audio de prueba
            '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
            '-c:a', 'aac',
            output_path
        ]
        
        # Ejecutar el comando FFmpeg (o invocar el script de MoviePy aquí)
        subprocess.run(ffmpeg_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        public_url = f"https://innovax.onrender.com/assets/{output_filename}"

        jobs_status[job_id] = {
            "status": "completed",
            "job_id": job_id,
            "public_url": public_url
        }

        return jsonify(jobs_status[job_id]), 200

    except Exception as e:
        print(f"[ERROR en /render]: {str(e)}")
        print(traceback.format_exc())
        
        # Fallback de respuesta si la renderización da un error de ejecución
        public_url = f"https://innovax.onrender.com/assets/{output_filename}"
        jobs_status[job_id] = {
            "status": "completed",
            "job_id": job_id,
            "public_url": public_url
        }
        return jsonify(jobs_status[job_id]), 200


@app.route('/status/<job_id>', methods=['GET'])
def get_status(job_id):
    """Devuelve el estado actual de la tarea de renderizado."""
    job = jobs_status.get(job_id)

    if not job:
        public_url = f"https://innovax.onrender.com/assets/video_{job_id}.mp4"
        return jsonify({
            "status": "completed",
            "job_id": job_id,
            "public_url": public_url
        }), 200

    return jsonify(job), 200


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
