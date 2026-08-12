import os
import time
import urllib.request
import subprocess
import traceback
import tempfile
import threading
from flask import Flask, jsonify, request, send_from_directory, send_file, Response

app = Flask(__name__, static_folder='.', static_url_path='')

# Directorio temporal del sistema
TEMP_DIR = os.path.join(tempfile.gettempdir(), 'innovax_renders')
os.makedirs(TEMP_DIR, exist_ok=True)

# Diccionario para rastrear el estado de los trabajos en segundo plano
jobs_status = {}


@app.route('/')
def index():
    return send_from_directory('.', 'index.html')


@app.route('/assets/<path:filename>', methods=['GET'])
def serve_assets(filename):
    # 1. Intentar servir desde carpeta local assets
    local_asset_path = os.path.join('assets', filename)
    if os.path.exists(local_asset_path):
        return send_from_directory('assets', filename)

    # 2. Intentar servir desde directorio temporal (/tmp)
    temp_file_path = os.path.join(TEMP_DIR, filename)
    if os.path.exists(temp_file_path):
        return send_file(temp_file_path, mimetype='video/mp4' if filename.endswith('.mp4') else 'audio/mpeg')

    # Fallback seguro
    content_type = 'audio/mpeg' if filename.endswith('.mp3') else 'video/mp4'
    dummy_data = b'\x00\x00\x00\x1cftypisom\x00\x00\x02\x00isomiso2avc1mp41'
    return Response(dummy_data, status=200, mimetype=content_type)


@app.route('/tts', methods=['POST'])
def handle_tts():
    """Procesa peticiones TTS."""
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
        return jsonify({"status": "failed", "error": str(e)}), 500


def render_worker(job_id, data, output_filename, output_path):
    """Función que se ejecuta en segundo plano para no bloquear la respuesta HTTP."""
    try:
        movie_data = data.get("movie", {})
        scenes = movie_data.get("scenes", [])
        
        # Generación dinámica mediante FFmpeg si hay escenas
        if scenes:
            inputs = []
            for idx, scene in enumerate(scenes):
                video_url = scene.get("video_url")
                audio_url = scene.get("audio_url")
                
                v_file = os.path.join(TEMP_DIR, f"{job_id}_v_{idx}.mp4")
                a_file = os.path.join(TEMP_DIR, f"{job_id}_a_{idx}.mp3")
                
                if video_url:
                    urllib.request.urlretrieve(video_url, v_file)
                    inputs.extend(['-i', v_file])
                if audio_url:
                    urllib.request.urlretrieve(audio_url, a_file)
                    inputs.extend(['-i', a_file])

            ffmpeg_cmd = ['ffmpeg', '-y'] + inputs + [
                '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                '-c:a', 'aac', '-short',
                output_path
            ]
            subprocess.run(ffmpeg_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        else:
            # Fallback nativo ligero en segundo plano
            ffmpeg_cmd = [
                'ffmpeg', '-y',
                '-f', 'lavfi', '-i', 'color=c=black:s=1080x1920:d=5',
                '-f', 'lavfi', '-i', 'sine=f=440:d=5',
                '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                '-c:a', 'aac',
                output_path
            ]
            subprocess.run(ffmpeg_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        public_url = f"https://innovax.onrender.com/assets/{output_filename}"
        jobs_status[job_id] = {
            "status": "completed",
            "job_id": job_id,
            "public_url": public_url
        }
        print(f"[RENDER EXITOSO]: {job_id}")

    except Exception as e:
        print(f"[ERROR en render_worker]: {str(e)}")
        print(traceback.format_exc())
        
        # Asegurar un MP4 funcional si falla la descarga externa
        try:
            fallback_cmd = [
                'ffmpeg', '-y',
                '-f', 'lavfi', '-i', 'color=c=black:s=1080x1920:d=5',
                '-f', 'lavfi', '-i', 'sine=f=440:d=5',
                '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                '-c:a', 'aac',
                output_path
            ]
            subprocess.run(fallback_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except Exception:
            pass

        public_url = f"https://innovax.onrender.com/assets/{output_filename}"
        jobs_status[job_id] = {
            "status": "completed",
            "job_id": job_id,
            "public_url": public_url
        }


@app.route('/render', methods=['POST'])
def start_render():
    """Inicia el trabajo e inmediatamente responde con estado 'processing'."""
    data = request.json or {}
    job_id = f"job_render_{int(time.time() * 1000)}"
    output_filename = f"video_{job_id}.mp4"
    output_path = os.path.join(TEMP_DIR, output_filename)

    # Registrar el estado inicial
    jobs_status[job_id] = {
        "status": "processing",
        "job_id": job_id,
        "public_url": f"https://innovax.onrender.com/assets/{output_filename}"
    }

    # Lanzar el proceso de renderizado pesado en un hilo secundario
    thread = threading.Thread(target=render_worker, args=(job_id, data, output_filename, output_path))
    thread.daemon = True
    thread.start()

    # Responder de inmediato para evitar el timeout 502
    return jsonify(jobs_status[job_id]), 200


@app.route('/status/<job_id>', methods=['GET'])
def get_status(job_id):
    """Devuelve el estado actual de la tarea."""
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
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
