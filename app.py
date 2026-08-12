import threading
import time
import traceback
from flask import Flask, jsonify, request

app = Flask(__name__)

# Diccionario en memoria (o base de datos/Redis) para seguir los trabajos
jobs_status = {}

def async_render_task(job_id, payload):
    """
    Función que ejecuta el renderizado pesado en segundo plano.
    """
    jobs_status[job_id] = {"status": "processing", "started_at": time.time()}
    
    try:
        # --- AQUÍ VA TU LÓGICA DE RENDERIZADO / TTS / FFMPEG ---
        # Ejemplo: ejecutar_ffmpeg_o_tts(payload)
        
        # Simulación / Ejecución real de renderizado
        result_url = "https://tu-servidor.com/video_renderizado.mp4"
        
        # Guardar resultado exitoso
        jobs_status[job_id] = {
            "status": "completed",
            "result_url": result_url
        }

    except Exception as e:
        # Capturar la excepción explícitamente para evitar tareas huérfanas
        error_msg = str(e)
        print(f"[ERROR Job {job_id}]: {error_msg}")
        print(traceback.format_exc())
        
        jobs_status[job_id] = {
            "status": "failed",
            "error": error_msg
        }

@app.route('/render', methods=['POST'])
def start_render():
    data = request.json or {}
    job_id = f"job_{int(time.time() * 1000)}"
    
    # Iniciar la tarea pesada en un hilo secundario para NO bloquear Gunicorn
    thread = threading.Thread(target=async_render_task, args=(job_id, data))
    thread.daemon = True
    thread.start()

    return jsonify({"job_id": job_id, "status": "processing"}), 200

@app.route('/status/<job_id>', methods=['GET'])
def get_status(job_id):
    job = jobs_status.get(job_id)
    
    # 1. Manejar caso donde la tarea no existe o expiró
    if not job:
        return jsonify({"error": "Tarea no encontrada o expirada"}), 404
        
    # 2. Control de timeout interno (ej. si lleva más de 10 minutos procesando)
    if job.get("status") == "processing":
        elapsed = time.time() - job.get("started_at", time.time())
        if elapsed > 600: # 10 minutos
            job["status"] = "failed"
            job["error"] = "Timeout: El renderizado tardó demasiado tiempo."

    return jsonify(job), 200
