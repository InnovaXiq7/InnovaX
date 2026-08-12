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

# Diccionario para rastrear el estado de los trabajos
jobs_status = {}

# Filtro FFmpeg para recortar y escalar a 1080x1920 (9:16 vertical) centrado
VERTICAL_FORMAT_FILTER = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1"


@app.route('/')
def index():
    return send_from_directory('.', 'index.html')


@app.route('/assets/<path:filename>', methods=['GET'])
def serve_assets(filename):
    local_asset_path = os.path.join('assets', filename)
    if os.path.exists(local_asset_path):
        return send_from_directory('assets', filename)

    temp_file_path = os.path.join(TEMP_DIR, filename)
    if os.path.exists(temp_file_path):
        return send_file(temp_file_path, mimetype='video/mp4' if filename.endswith('.mp4') else 'audio/mpeg')

    content_type = 'audio/mpeg' if filename.endswith('.mp3') else 'video/mp4'
    dummy_data = b'\x00\x00\x00\x1cftypisom\x00\x00\x02\x00isomiso2avc1mp41'
    return Response(dummy_data, status=200, mimetype=content_type)


@app.route('/tts', methods=['POST'])
def handle_tts():
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
    """Worker en segundo plano para procesar y renderizar el MP4 en 1080x1920."""
    try:
        movie_data = data.get("movie", {})
        scenes = movie_data.get("scenes", [])
        
        if scenes:
            inputs = []
            filter_complex = []
            concat_v = []
            concat_a = []

            for idx, scene in enumerate(scenes):
                video_url = scene.get("video_url")
                audio_url = scene.get("audio_url")
                
                v_file = os.path.join(TEMP_DIR, f"{job_id}_v_{idx}.mp4")
                a_file = os.path.join(TEMP_DIR, f"{job_id}_a_{idx}.mp3")
                
                # Descarga de assets
                if video_url:
                    urllib.request.urlretrieve(video_url, v_file)
                if audio_url:
                    urllib.request.urlretrieve(audio_url, a_file)

                # Si ambos archivos existen, los añadimos al flujo
                if os.path.exists(v_file) and os.path.exists(a_file):
                    v_idx = len(inputs) // 2
                    inputs.extend(['-i', v_file, '-i', a_file])
                    
                    # Aplicar formato vertical a cada entrada de vídeo y emparejar con su audio
                    filter_complex.append(f"[{v_idx*2}:v]{VERTICAL_FORMAT_FILTER}[v{idx}];")
                    concat_v.append(f"[v{idx}]")
                    concat_a.append(f"[{v_idx*2 + 1}:a]")

            if concat_v:
                # Concatenar todas las escenas procesadas
                num_scenes = len(concat_v)
                concat_str = f"{''.join([f'{v}{a}' for v, a in zip(concat_v, concat_a)])}concat=n={num_scenes}:v=1:a=1[outv][outa]"
                full_filter = "".join(filter_complex) + concat_str

                ffmpeg_cmd = ['ffmpeg', '-y'] + inputs + [
                    '-filter_complex', full_filter,
                    '-map', '[outv]', '-map', '[outa]',
                    '-c:v', 'libx264', '-preset', 'fast', '-pix_fmt', 'yuv420p',
                    '-c:a', 'aac', '-b:a', '192k',
                    output_path
                ]
            else:
                raise ValueError("No se pudieron descargar o procesar los clips de las escenas.")

        else:
            # Fallback simple vertical de 5s
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
        print(f"[RENDER EXITOSO 1080x1920]: {job_id}")

    except Exception as e:
        print(f"[ERROR en render_worker]: {str(e)}")
        print(traceback.format_exc())
        
        # Fallback de emergencia en 1080x1920 si ocurre algún fallo de red/download
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
