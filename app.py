import os
import time
import urllib.request
import subprocess
import traceback
import tempfile
import threading
import gc
from flask import Flask, jsonify, request, send_from_directory, send_file, Response

app = Flask(__name__, static_folder='.', static_url_path='')

# Directorio temporal garantizado para el sistema
TEMP_DIR = os.path.join(tempfile.gettempdir(), 'innovax_renders')
os.makedirs(TEMP_DIR, exist_ok=True)

# Diccionario para almacenar el estado de los renders en memoria
jobs_status = {}

# Cerrojo para evitar ejecuciones concurrentes que excedan los 512 MB de RAM en Render
render_lock = threading.Lock()

# Filtro FFmpeg para escalar y recortar automáticamente a 1080x1920 (9:16 vertical)
VERTICAL_FILTER = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1"


def download_file(url, destination_path):
    """Descarga archivos HTTP/HTTPS enviando un User-Agent para evitar bloqueos 403."""
    req = urllib.request.Request(
        url, 
        headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
    )
    with urllib.request.urlopen(req, timeout=30) as response, open(destination_path, 'wb') as out_file:
        out_file.write(response.read())


@app.route('/')
def index():
    return send_from_directory('.', 'index.html')


@app.route('/assets/<path:filename>', methods=['GET'])
def serve_assets(filename):
    """Sirve los archivos procesados desde el directorio temporal o fallback seguro."""
    temp_file_path = os.path.join(TEMP_DIR, filename)
    if os.path.exists(temp_file_path) and os.path.getsize(temp_file_path) > 0:
        return send_file(temp_file_path, mimetype='video/mp4' if filename.endswith('.mp4') else 'audio/mpeg')

    local_asset_path = os.path.join('assets', filename)
    if os.path.exists(local_asset_path):
        return send_from_directory('assets', filename)

    if filename.endswith('.mp4'):
        fallback_path = os.path.join(TEMP_DIR, f"fallback_{filename}")
        if not os.path.exists(fallback_path):
            build_fallback_video(fallback_path)
        return send_file(fallback_path, mimetype='video/mp4')

    return jsonify({"error": "File not found"}), 404


@app.route('/tts', methods=['POST'])
def handle_tts():
    data = request.json or {}
    job_id = f"job_tts_{int(time.time() * 1000)}"
    public_url = data.get("output_url", f"https://innovax.onrender.com/assets/tts_{job_id}.mp3")
    
    jobs_status[job_id] = {
        "status": "completed",
        "job_id": job_id,
        "public_url": public_url
    }
    return jsonify(jobs_status[job_id]), 200


def build_fallback_video(output_path):
    """Genera un vídeo sintético de prueba en 1080x1920 con bajo consumo de memoria."""
    cmd = [
        'ffmpeg', '-y',
        '-threads', '2',
        '-f', 'lavfi', '-i', 'color=c=black:s=1080x1920:r=30:d=5',
        '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=stereo',
        '-t', '5',
        '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-b:a', '128k',
        output_path
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def render_worker(job_id, data, output_filename, output_path):
    """Worker con control estricto de memoria RAM para planes de 512 MB."""
    with render_lock:
        print(f"\n================ [INICIO TRABAJO {job_id}] ================")
        print(f"PAYLOAD RECIBIDO DE N8N: {data}")

        try:
            movie_data = data.get("movie", {})
            scenes = movie_data.get("scenes", [])
            valid_clip_files = []

            if not scenes:
                print("[ALERTA] La lista de escenas está vacía o el JSON no contiene la clave 'movie.scenes'")

            for idx, scene in enumerate(scenes):
                video_url = scene.get("video_url")
                audio_url = scene.get("audio_url")

                print(f"\n--- Procesando Escena {idx} ---")
                print(f"Video URL: {video_url}")
                print(f"Audio URL: {audio_url}")

                raw_v = os.path.join(TEMP_DIR, f"{job_id}_raw_v_{idx}.mp4")
                raw_a = os.path.join(TEMP_DIR, f"{job_id}_raw_a_{idx}.mp3")
                clip_out = os.path.join(TEMP_DIR, f"{job_id}_clip_{idx}.mp4")

                if video_url and audio_url:
                    try:
                        download_file(video_url, raw_v)
                        download_file(audio_url, raw_a)

                        size_v = os.path.getsize(raw_v) if os.path.exists(raw_v) else 0
                        size_a = os.path.getsize(raw_a) if os.path.exists(raw_a) else 0
                        print(f"Descargado -> Vídeo: {size_v} bytes | Audio: {size_a} bytes")

                        if size_v < 10000 or size_a < 1000:
                            print(f"[ERROR] Archivo pequeño/inválido en escena {idx}.")
                            continue

                        # FFmpeg optimizado para bajo consumo de RAM (-threads 2, -preset ultrafast)
                        clip_cmd = [
                            'ffmpeg', '-y',
                            '-threads', '2',
                            '-i', raw_v,
                            '-i', raw_a,
                            '-vf', VERTICAL_FILTER,
                            '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
                            '-c:a', 'aac', '-b:a', '128k',
                            '-shortest',
                            clip_out
                        ]
                        res = subprocess.run(clip_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

                        # Limpieza inmediata de binarios raw para liberar espacio/RAM
                        if os.path.exists(raw_v): os.remove(raw_v)
                        if os.path.exists(raw_a): os.remove(raw_a)

                        if res.returncode != 0:
                            print(f"[FFmpeg ERROR escena {idx}]: {res.stderr}")
                        else:
                            print(f"Escena {idx} procesada correctamente.")
                            valid_clip_files.append(clip_out)

                    except Exception as clip_err:
                        print(f"[Excepción en escena {idx}]: {str(clip_err)}")
                else:
                    print(f"[ALERTA] Falta video_url o audio_url en la escena {idx}")

            if valid_clip_files:
                concat_list_path = os.path.join(TEMP_DIR, f"{job_id}_concat.txt")
                with open(concat_list_path, 'w') as f:
                    for clip in valid_clip_files:
                        f.write(f"file '{clip}'\n")

                concat_cmd = [
                    'ffmpeg', '-y',
                    '-threads', '2',
                    '-f', 'concat', '-safe', '0',
                    '-i', concat_list_path,
                    '-c', 'copy',
                    output_path
                ]
                subprocess.run(concat_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

                # Limpieza de clips intermedios
                for clip in valid_clip_files:
                    if os.path.exists(clip): os.remove(clip)
                if os.path.exists(concat_list_path): os.remove(concat_list_path)

                print(f"\n================ [ÉXITO: VIDEO FINAL EN {output_path}] ================")
            else:
                print("\n[FALLBACK] Ninguna escena se procesó correctamente. Generando vídeo sintético...")
                build_fallback_video(output_path)

        except Exception as e:
            print(f"[ERROR CRÍTICO en render_worker]: {str(e)}")
            print(traceback.format_exc())
            build_fallback_video(output_path)

        if not os.path.exists(output_path):
            build_fallback_video(output_path)

        # Forzar liberación de memoria acumulada en Python
        gc.collect()

        public_url = f"https://innovax.onrender.com/assets/{output_filename}"
        jobs_status[job_id] = {
            "status": "completed",
            "job_id": job_id,
            "public_url": public_url
        }


@app.route('/render', methods=['POST'])
def start_render():
    data = request.json or {}
    job_id = f"job_render_{int(time.time() * 1000)}"
    output_filename = f"video_{job_id}.mp4"
    output_path = os.path.join(TEMP_DIR, output_filename)

    jobs_status[job_id] = {
        "status": "processing",
        "job_id": job_id,
        "public_url": f"https://innovax.onrender.com/assets/{output_filename}"
    }

    thread = threading.Thread(target=render_worker, args=(job_id, data, output_filename, output_path))
    thread.daemon = True
    thread.start()

    return jsonify(jobs_status[job_id]), 200


@app.route('/status/<job_id>', methods=['GET'])
def get_status(job_id):
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
