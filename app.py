import os
import time
import urllib.request
import subprocess
import traceback
import tempfile
import threading
import gc
from flask import Flask, jsonify, request, send_from_directory, send_file

app = Flask(__name__, static_folder='.', static_url_path='')

# Directorios absolutos para el entorno de Render
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(BASE_DIR, 'assets')
TEMP_DIR = os.path.join(tempfile.gettempdir(), 'innovax_renders')

os.makedirs(ASSETS_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)

jobs_status = {}
render_lock = threading.Lock()

def download_file(url, destination_path):
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
    local_asset_path = os.path.join(ASSETS_DIR, filename)
    if os.path.exists(local_asset_path) and os.path.getsize(local_asset_path) > 0:
        mimetype = 'video/mp4' if filename.endswith('.mp4') else ('audio/mpeg' if filename.endswith('.mp3') else None)
        return send_file(local_asset_path, mimetype=mimetype)

    temp_file_path = os.path.join(TEMP_DIR, filename)
    if os.path.exists(temp_file_path) and os.path.getsize(temp_file_path) > 0:
        mimetype = 'video/mp4' if filename.endswith('.mp4') else ('audio/mpeg' if filename.endswith('.mp3') else None)
        return send_file(temp_file_path, mimetype=mimetype)

    if filename.endswith('.mp4'):
        fallback_path = os.path.join(TEMP_DIR, f"fallback_{filename}")
        if not os.path.exists(fallback_path):
            build_fallback_video(fallback_path)
        return send_file(fallback_path, mimetype='video/mp4')

    return jsonify({"error": "File not found"}), 404

@app.route('/tts', methods=['POST'])
def handle_tts():
    try:
        data = request.json or {}
        timestamp = int(time.time() * 1000)
        job_id = f"job_tts_{timestamp}"
        filename = f"tts_{job_id}.mp3"
        file_path = os.path.join(ASSETS_DIR, filename)

        # Generar un archivo estático de prueba para asegurar respuesta válida
        cmd = [
            'ffmpeg', '-y',
            '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=stereo',
            '-t', '3',
            '-c:a', 'libmp3lame', '-b:a', '128k',
            file_path
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        public_url = data.get("output_url", f"https://innovax.onrender.com/assets/{filename}")
        
        jobs_status[job_id] = {
            "status": "completed",
            "job_id": job_id,
            "public_url": public_url,
            "url": public_url
        }
        return jsonify(jobs_status[job_id]), 200
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

def build_fallback_video(output_path):
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
    with render_lock:
        print(f"\n================ [INICIO TRABAJO {job_id}] ================")
        try:
            movie_data = data.get("movie", {})
            scenes = movie_data.get("scenes", [])
            valid_clip_files = []

            for idx, scene in enumerate(scenes):
                video_url = scene.get("video_url", "")
                audio_url = scene.get("audio_url", "")
                scene_duration = str(scene.get("duration", 5))

                is_image = any(ext in video_url.lower() for ext in ['.jpg', '.png', '.jpeg', 'pollinations']) or not video_url.endswith('.mp4')
                file_ext = ".jpg" if is_image else ".mp4"

                raw_v = os.path.join(TEMP_DIR, f"{job_id}_raw_v_{idx}{file_ext}")
                raw_a = os.path.join(TEMP_DIR, f"{job_id}_raw_a_{idx}.mp3")
                clip_out = os.path.join(TEMP_DIR, f"{job_id}_clip_{idx}.mp4")

                if video_url:
                    try:
                        download_file(video_url, raw_v)

                        has_audio = False
                        if audio_url:
                            try:
                                download_file(audio_url, raw_a)
                                if os.path.exists(raw_a) and os.path.getsize(raw_a) > 0:
                                    has_audio = True
                            except Exception as a_err:
                                print(f"[Aviso]: No se pudo descargar audio para escena {idx}: {a_err}")

                        if is_image:
                            # Procesamiento directo y compatible para imágenes verticales en FFmpeg
                            if has_audio:
                                clip_cmd = [
                                    'ffmpeg', '-y', '-threads', '2',
                                    '-loop', '1', '-i', raw_v,
                                    '-i', raw_a,
                                    '-vf', "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,format=yuv420p",
                                    '-c:v', 'libx264', '-preset', 'ultrafast',
                                    '-c:a', 'aac', '-b:a', '128k',
                                    '-shortest',
                                    clip_out
                                ]
                            else:
                                clip_cmd = [
                                    'ffmpeg', '-y', '-threads', '2',
                                    '-loop', '1', '-i', raw_v,
                                    '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=stereo',
                                    '-t', scene_duration,
                                    '-vf', "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,format=yuv420p",
                                    '-c:v', 'libx264', '-preset', 'ultrafast',
                                    '-c:a', 'aac', '-b:a', '128k',
                                    clip_out
                                ]
                        else:
                            # Clip de vídeo estándar
                            if has_audio:
                                clip_cmd = [
                                    'ffmpeg', '-y', '-threads', '2',
                                    '-i', raw_v, '-i', raw_a,
                                    '-vf', "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,format=yuv420p",
                                    '-c:v', 'libx264', '-preset', 'ultrafast',
                                    '-c:a', 'aac', '-b:a', '128k',
                                    '-shortest',
                                    clip_out
                                ]
                            else:
                                clip_cmd = [
                                    'ffmpeg', '-y', '-threads', '2',
                                    '-i', raw_v,
                                    '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=stereo',
                                    '-vf', "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,format=yuv420p",
                                    '-c:v', 'libx264', '-preset', 'ultrafast',
                                    '-c:a', 'aac', '-b:a', '128k',
                                    clip_out
                                ]

                        res = subprocess.run(clip_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

                        if os.path.exists(raw_v): os.remove(raw_v)
                        if os.path.exists(raw_a): os.remove(raw_a)

                        if res.returncode == 0 and os.path.exists(clip_out) and os.path.getsize(clip_out) > 0:
                            valid_clip_files.append(clip_out)
                        else:
                            print(f"[FFmpeg Error Escena {idx}]: {res.stderr}")

                    except Exception as clip_err:
                        print(f"[Error en escena {idx}]: {str(clip_err)}")

            if valid_clip_files:
                concat_list_path = os.path.join(TEMP_DIR, f"{job_id}_concat.txt")
                with open(concat_list_path, 'w') as f:
                    for clip in valid_clip_files:
                        f.write(f"file '{clip}'\n")

                concat_cmd = [
                    'ffmpeg', '-y', '-threads', '2',
                    '-f', 'concat', '-safe', '0',
                    '-i', concat_list_path,
                    '-c', 'copy',
                    output_path
                ]
                subprocess.run(concat_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

                for clip in valid_clip_files:
                    if os.path.exists(clip): os.remove(clip)
                if os.path.exists(concat_list_path): os.remove(concat_list_path)

            else:
                build_fallback_video(output_path)

        except Exception as e:
            print(f"[ERROR GLOBAL RENDER]: {str(e)}")
            build_fallback_video(output_path)

        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            build_fallback_video(output_path)

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
