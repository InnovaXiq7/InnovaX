import os
import time
import threading
import traceback
from flask import Flask, jsonify, request, send_from_directory

app = Flask(__name__, static_folder='.', static_url_path='')

# Diccionario en memoria para almacenar el estado de los trabajos/renderizados
jobs_status = {}

def background_render_process(job_id, payload):
    """
    Función que ejecuta el proceso pesado (TTS/FFmpeg/Render) en segundo plano.
    Captura cualquier error y actualiza el estado para que no quede colgado.
    """
    jobs_status[job_id] = {
        "status": "processing",
        "started_at": time.time(),
        "result_url": None,
        "error": None
    }
    
    try:
        # -------------------------------------------------------------
        # AQUÍ VA TU LÓGICA REA L DE RENDERIZADO / FFMPEG / TTS
        # Ejemplo:
        # process_ffmpeg_video(payload)
        # -------------------------------------------------------------
        
        # Simulación de resultado (reemplazar con la ruta/URL real de tu vídeo)
        result_url = payload.get("output_url", f"/assets/output_{job_id}.mp4")

        # Marcar la tarea como completada
        jobs_status[job_id]["status"] = "completed"
        jobs_status[job_id]["result_url"] = result_url

    except Exception as e:
        # Si ocurre un error, registrarlo y marcar la tarea como 'failed'
        error_msg = str(e)
        print(f"[ERROR en Job {job_id}]: {error_msg}")
        print(traceback.format_exc())
        
        jobs_status[job_id]["status"] = "failed"
        jobs_status[job_id]["error"] = error_msg


@app.route('/')
def index():
    return send_from_directory('.', 'index.html')


@app.route('/render', methods=['POST'])
def start_render():
    """Inicia una tarea de renderizado en segundo plano."""
    data = request.json or {}
    job_id = f"job_{int(time.time() * 1000)}"

    # Iniciar la tarea en un hilo independiente (Thread)
    thread = threading.Thread(target=background_render_process, args=(job_id, data))
    thread.daemon = True
    thread.start()

    return jsonify({"job_id": job_id, "status": "processing"}), 200


@app.route('/status/<job_id>', methods=['GET'])
def get_status(job_id):
    """Devuelve el estado de la tarea solicitada por el frontend."""
    job = jobs_status.get(job_id)

    # 1. Si el job_id no existe, responder 404
    if not job:
        return jsonify({"status": "failed", "error": "Tarea no encontrada o expirada"}), 404

    # 2. Control de Timeout si el proceso lleva más de 10 minutos (600s)
    if job["status"] == "processing":
        elapsed = time.time() - job.get("started_at", time.time())
        if elapsed > 600:
            job["status"] = "failed"
            job["error"] = "El renderizado ha superado el tiempo límite (Timeout)."

    return jsonify(job), 200


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
